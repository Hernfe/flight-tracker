from datetime import date, timedelta

import pytest

from app.core.events import (
    PriceBucketUpdated,
    clear_subscribers,
    publish,
    subscribe,
)
from app.modules.alerts.subscriber import handle_price_bucket_updated
from app.modules.flights.models import ApiResponseRaw
from app.workers import tasks


@pytest.fixture(autouse=True)
def _clean_subscribers():
    clear_subscribers()
    yield
    clear_subscribers()


def _offer(amount, carrier, segments=1, currency="EUR"):
    return {
        "total_amount": str(amount),
        "total_currency": currency,
        "owner": {"iata_code": carrier},
        "slices": [{"segments": [{} for _ in range(segments)]}],
    }


async def _make_raw(session, offers, *, poll_target_id=None):
    dep = date.today() + timedelta(days=30)
    raw = ApiResponseRaw(
        source="duffel",
        request_params={
            "origin": "HEL",
            "destination": "LHR",
            "departure_date": dep.isoformat(),
            "return_date": None,
            "cabin": "economy",
            "source": "duffel",
        },
        response_body={"data": {"offers": offers}},
        http_status=200,
        poll_target_id=poll_target_id,
        request_id="req-test",
    )
    session.add(raw)
    await session.commit()
    await session.refresh(raw)
    return raw


async def test_single_bucket_publishes_one_event(db_session):
    captured: list[PriceBucketUpdated] = []
    subscribe(captured.append)
    # All direct (1 segment -> 0 stops): one bucket.
    raw = await _make_raw(
        db_session, [_offer(150, "AY"), _offer(120, "BA"), _offer(200, "AY")]
    )

    await tasks.normalize_response({}, raw.id)

    assert len(captured) == 1
    event = captured[0]
    assert isinstance(event, PriceBucketUpdated)
    assert event.origin == "HEL"
    assert event.destination == "LHR"
    assert event.cabin == "economy"
    assert event.stops_bucket == 0
    assert event.price_cents == 12000  # cheapest of the bucket
    assert event.carrier_code == "BA"  # winning carrier
    assert event.currency == "EUR"


async def test_one_event_per_bucket(db_session):
    captured: list[PriceBucketUpdated] = []
    subscribe(captured.append)
    raw = await _make_raw(
        db_session,
        [
            _offer(150, "AY", segments=1),  # 0 stops
            _offer(120, "BA", segments=2),  # 1 stop
            _offer(100, "LH", segments=3),  # 2 stops
        ],
    )

    await tasks.normalize_response({}, raw.id)

    assert sorted(e.stops_bucket for e in captured) == [0, 1, 2]


async def test_empty_response_publishes_nothing(db_session):
    captured: list[PriceBucketUpdated] = []
    subscribe(captured.append)
    raw = await _make_raw(db_session, [])

    await tasks.normalize_response({}, raw.id)

    assert captured == []


async def test_rolled_back_normalization_publishes_nothing(db_session, monkeypatch):
    captured: list[PriceBucketUpdated] = []
    subscribe(captured.append)
    raw = await _make_raw(db_session, [_offer(150, "AY")])

    async def boom(self):
        raise RuntimeError("commit failed")

    # Force the normalizer's commit to fail; nothing must be published.
    monkeypatch.setattr(
        "sqlalchemy.ext.asyncio.AsyncSession.commit", boom, raising=True
    )

    with pytest.raises(RuntimeError):
        await tasks.normalize_response({}, raw.id)

    assert captured == []


async def test_stub_subscriber_consumes_without_error():
    # The placeholder alert subscriber must accept events cleanly.
    subscribe(handle_price_bucket_updated)
    event = PriceBucketUpdated(
        poll_target_id=1,
        origin="HEL",
        destination="LHR",
        departure_date=date.today(),
        return_date=None,
        cabin="economy",
        stops_bucket=0,
        price_cents=12000,
        currency="EUR",
        carrier_code="BA",
        observed_at=None,
    )
    # Should not raise.
    await publish(event)
