from datetime import date, datetime, timezone
from typing import Any

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import func, select

from app.config import settings
from app.core.db import SessionLocal
from app.core.events import PriceBucketUpdated, publish
from app.core.observability import init_sentry
from app.integrations.duffel import DuffelClient
from app.modules.alerts.subscriber import register as register_alert_subscriber
from app.modules.flights.coverage import run_maintainer
from app.modules.flights.models import ApiResponseRaw, FlightOffer, PollTarget
from app.modules.flights.poll import has_tracking_reason, poll_interval
from app.modules.price_history.models import PriceHistory

# Arq worker context dict passed to every task/cron handler.
type WorkerContext = dict[str, Any]


def parse_offers(response_body: dict[str, Any]) -> list[dict[str, Any]]:
    offers = response_body.get("data", {}).get("offers", [])
    parsed: list[dict[str, Any]] = []
    for offer in offers:
        amount = offer.get("total_amount")
        if amount is None:
            continue
        try:
            price_cents = round(float(amount) * 100)
        except (TypeError, ValueError):
            continue
        # Stops bucket: the worst (most connections) slice, capped at 2 meaning
        # "two or more". A round trip direct out / one stop back is bucket 1.
        stops = 0
        for sl in offer.get("slices", []):
            segments = sl.get("segments", [])
            stops = max(stops, len(segments) - 1)
        stops = min(max(stops, 0), 2)

        # Marketing carrier of the offer: the owning airline, falling back to the
        # first segment's marketing carrier when the owner is absent.
        owner = offer.get("owner") or {}
        carrier = owner.get("iata_code")
        if carrier is None:
            for sl in offer.get("slices", []):
                segments = sl.get("segments", [])
                if segments:
                    mc = segments[0].get("marketing_carrier") or {}
                    carrier = mc.get("iata_code")
                    break

        parsed.append(
            {
                "carrier": carrier,
                "price_cents": price_cents,
                "currency": offer.get("total_currency", "EUR"),
                "stops": stops,
            }
        )
    return parsed


async def poll_target(ctx: WorkerContext, target_id: int) -> None:
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
        tracking = await has_tracking_reason(session, target.id)
        target.last_polled_at = now
        target.next_poll_at = now + poll_interval(
            days_to_departure, has_tracking=tracking
        )

        await session.commit()
        raw_id = raw.id

    await ctx["redis"].enqueue_job("normalize_response", raw_id)


async def normalize_response(ctx: WorkerContext, raw_id: int) -> None:
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

        cabin = params["cabin"]

        # Per-airline layer: cheapest offer for every (carrier, stops, cabin)
        # combination present in the response. cabin is constant per search.
        best_by_combo: dict[tuple[str | None, int], dict[str, Any]] = {}
        for p in parsed:
            key = (p["carrier"], p["stops"])
            current = best_by_combo.get(key)
            if current is None or p["price_cents"] < current["price_cents"]:
                best_by_combo[key] = p

        for (carrier, stops), p in best_by_combo.items():
            session.add(
                FlightOffer(
                    raw_response_id=raw.id,
                    origin=params["origin"],
                    destination=params["destination"],
                    departure_date=dep,
                    return_date=ret,
                    cabin=cabin,
                    stops=stops,
                    carrier=carrier,
                    fare_brand=None,
                    price_cents=p["price_cents"],
                    currency=p["currency"],
                    source=raw.source,
                    observed_at=raw.observed_at,
                )
            )

        # Fast lane: one row per (stops, cabin) bucket holding the single
        # overall-cheapest offer across all carriers, recording the winner.
        by_bucket: dict[int, list[dict[str, Any]]] = {}
        for p in parsed:
            by_bucket.setdefault(p["stops"], []).append(p)

        events: list[PriceBucketUpdated] = []
        for stops, group in by_bucket.items():
            ranked = sorted(group, key=lambda x: x["price_cents"])
            cheapest = ranked[0]
            top_n = ranked[:5]
            mean_top_n = round(sum(x["price_cents"] for x in top_n) / len(top_n))
            session.add(
                PriceHistory(
                    origin=params["origin"],
                    destination=params["destination"],
                    departure_date=dep,
                    return_date=ret,
                    cabin=cabin,
                    stops=stops,
                    carrier=cheapest["carrier"],
                    source=raw.source,
                    cheapest_price_cents=cheapest["price_cents"],
                    mean_top_n_price_cents=mean_top_n,
                    n_offers=len(group),
                    currency=cheapest["currency"],
                    observed_at=raw.observed_at,
                )
            )
            events.append(
                PriceBucketUpdated(
                    poll_target_id=raw.poll_target_id,
                    origin=params["origin"],
                    destination=params["destination"],
                    departure_date=dep,
                    return_date=ret,
                    cabin=cabin,
                    stops_bucket=stops,
                    price_cents=cheapest["price_cents"],
                    currency=cheapest["currency"],
                    carrier_code=cheapest["carrier"],
                    observed_at=raw.observed_at,
                )
            )

        await session.commit()

    # Publish only after the write is durable; a rolled-back normalization emits
    # nothing. The normalizer itself never touches wishlists/users/alerts.
    for event in events:
        await publish(event)


async def schedule_due_polls(ctx: WorkerContext) -> None:
    redis = ctx["redis"]
    now = datetime.now(timezone.utc)
    today = now.date()
    daily_key = f"duffel:searches:{today.isoformat()}"
    monthly_key = f"duffel:searches:{today.strftime('%Y-%m')}"
    daily_limit = settings.duffel_daily_search_budget
    monthly_limit = settings.duffel_monthly_search_budget

    async with SessionLocal() as session:
        # One cadence for all reasons: due targets, oldest first. Tracking gets
        # polled sooner via a tighter cadence, not via queue priority.
        result = await session.scalars(
            select(PollTarget)
            .where(PollTarget.active.is_(True), PollTarget.next_poll_at <= now)
            .order_by(PollTarget.next_poll_at.asc())
        )
        targets = list(result)

    for target in targets:
        daily_count = await redis.incr(daily_key)
        if daily_count == 1:
            await redis.expire(daily_key, 48 * 60 * 60)
        monthly_count = await redis.incr(monthly_key)
        if monthly_count == 1:
            await redis.expire(monthly_key, 40 * 24 * 60 * 60)

        # Budget guard: once the (daily or monthly) cap is hit, stop the batch.
        if daily_count > daily_limit or monthly_count > monthly_limit:
            await redis.decr(daily_key)
            await redis.decr(monthly_key)
            break

        await redis.enqueue_job("poll_target", target.id)


async def maintain_coverage(ctx: WorkerContext) -> None:
    async with SessionLocal() as session:
        await run_maintainer(session)
        await session.commit()


async def startup(ctx: WorkerContext) -> None:
    init_sentry(web=False)
    register_alert_subscriber()
    await schedule_due_polls(ctx)


class WorkerSettings:
    functions = [poll_target, normalize_response]
    cron_jobs = [
        # Due poller every 30 minutes; maintainer once daily.
        cron(schedule_due_polls, minute={0, 30}),
        cron(maintain_coverage, hour={3}, minute={15}),
    ]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
