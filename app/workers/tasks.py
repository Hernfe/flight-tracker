from datetime import date, datetime, timedelta, timezone

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import func, select

from app.config import settings
from app.core.db import SessionLocal
from app.integrations.duffel import DuffelClient
from app.modules.flights.models import ApiResponseRaw, FlightOffer, PollTarget
from app.modules.price_history.models import PriceHistory


def next_interval(days_to_departure: int) -> timedelta:
    if days_to_departure > 60:
        return timedelta(days=2)
    if days_to_departure > 14:
        return timedelta(days=1)
    if days_to_departure > 3:
        return timedelta(hours=12)
    return timedelta(hours=4)


def parse_offers(response_body: dict) -> list[dict]:
    offers = response_body.get("data", {}).get("offers", [])
    parsed: list[dict] = []
    for offer in offers:
        amount = offer.get("total_amount")
        if amount is None:
            continue
        try:
            price_cents = round(float(amount) * 100)
        except (TypeError, ValueError):
            continue
        slices = offer.get("slices", [])
        stops = 0
        if slices:
            segments = slices[0].get("segments", [])
            stops = max(len(segments) - 1, 0)
        owner = offer.get("owner") or {}
        parsed.append(
            {
                "carrier": owner.get("iata_code"),
                "price_cents": price_cents,
                "currency": offer.get("total_currency", "EUR"),
                "stops": stops,
            }
        )
    return parsed


async def poll_target(ctx: dict, target_id: int) -> None:
    async with SessionLocal() as session:
        target = await session.get(PollTarget, target_id)
        if target is None or not target.active:
            return

        dep = target.departure_date.isoformat()
        ret = target.return_date.isoformat() if target.return_date else None

        client = DuffelClient()
        status, body, request_id = await client.search_offers(
            origin=target.origin,
            destination=target.destination,
            departure_date=dep,
            return_date=ret,
            cabin_class=target.cabin,
        )

        raw = ApiResponseRaw(
            source=target.source,
            request_params={
                "origin": target.origin,
                "destination": target.destination,
                "departure_date": dep,
                "return_date": ret,
                "cabin": target.cabin,
                "source": target.source,
            },
            response_body=body,
            http_status=status,
            poll_target_id=target.id,
            request_id=request_id or "unknown",
        )
        session.add(raw)
        await session.flush()

        now = datetime.now(timezone.utc)
        days_to_departure = (target.departure_date - date.today()).days
        target.last_polled_at = now
        target.next_poll_at = now + next_interval(days_to_departure)

        await session.commit()
        raw_id = raw.id

    await ctx["redis"].enqueue_job("normalize_response", raw_id)


async def normalize_response(ctx: dict, raw_id: int) -> None:
    async with SessionLocal() as session:
        raw = await session.get(ApiResponseRaw, raw_id)
        if raw is None:
            return

        already = await session.scalar(
            select(func.count())
            .select_from(FlightOffer)
            .where(FlightOffer.raw_response_id == raw_id)
        )
        if already:
            return

        params = raw.request_params
        parsed = parse_offers(raw.response_body)
        if not parsed:
            return

        dep = date.fromisoformat(params["departure_date"])
        ret = (
            date.fromisoformat(params["return_date"])
            if params.get("return_date")
            else None
        )

        for p in parsed:
            session.add(
                FlightOffer(
                    raw_response_id=raw.id,
                    origin=params["origin"],
                    destination=params["destination"],
                    departure_date=dep,
                    return_date=ret,
                    cabin=params["cabin"],
                    stops=p["stops"],
                    carrier=p["carrier"],
                    fare_brand=None,
                    price_cents=p["price_cents"],
                    currency=p["currency"],
                    source=raw.source,
                    observed_at=raw.observed_at,
                )
            )

        prices = sorted(p["price_cents"] for p in parsed)
        cheapest = prices[0]
        top_n = prices[:5]
        mean_top_n = round(sum(top_n) / len(top_n))
        cheapest_currency = next(
            p["currency"] for p in parsed if p["price_cents"] == cheapest
        )

        session.add(
            PriceHistory(
                origin=params["origin"],
                destination=params["destination"],
                departure_date=dep,
                return_date=ret,
                cabin=params["cabin"],
                source=raw.source,
                cheapest_price_cents=cheapest,
                mean_top_n_price_cents=mean_top_n,
                n_offers=len(parsed),
                currency=cheapest_currency,
                observed_at=raw.observed_at,
            )
        )

        await session.commit()


async def schedule_due_polls(ctx: dict) -> None:
    async with SessionLocal() as session:
        now = datetime.now(timezone.utc)
        result = await session.scalars(
            select(PollTarget).where(
                PollTarget.active.is_(True), PollTarget.next_poll_at <= now
            )
        )
        targets = list(result)

    for target in targets:
        await ctx["redis"].enqueue_job("poll_target", target.id)


async def startup(ctx: dict) -> None:
    await schedule_due_polls(ctx)


class WorkerSettings:
    functions = [poll_target, normalize_response]
    cron_jobs = [cron(schedule_due_polls, minute=set(range(0, 60)))]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url)