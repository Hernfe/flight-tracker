from datetime import date, datetime, timedelta, timezone

import pytest

from app.config import settings
from app.modules.flights.models import PollTarget
from app.workers.tasks import schedule_due_polls
from tests.conftest import budget_keys

DAILY_TTL = 48 * 60 * 60
MONTHLY_TTL = 40 * 24 * 60 * 60


async def make_target(
    session,
    *,
    is_user_tracked: bool = False,
    due: bool = True,
    destination: str = "LHR",
) -> PollTarget:
    now = datetime.now(timezone.utc)
    next_poll_at = now - timedelta(minutes=5) if due else now + timedelta(days=1)
    target = PollTarget(
        origin="HEL",
        destination=destination,
        departure_date=date.today() + timedelta(days=30),
        is_user_tracked=is_user_tracked,
        next_poll_at=next_poll_at,
    )
    session.add(target)
    await session.commit()
    await session.refresh(target)
    return target


async def test_daily_budget_cap(db_session, spy_redis, redis_client, monkeypatch):
    monkeypatch.setattr(settings, "duffel_daily_search_budget", 3)
    monkeypatch.setattr(settings, "duffel_monthly_search_budget", 100)
    for i in range(5):
        await make_target(db_session, destination=f"D{i}")

    await schedule_due_polls({"redis": spy_redis})

    daily_key, monthly_key = budget_keys()
    assert len(spy_redis.enqueued) == 3
    assert all(name == "poll_target" for name, _ in spy_redis.enqueued)
    # Daily counter stops exactly at the limit, never above it.
    assert await redis_client.get(daily_key) == "3"
    # Monthly counter reflects only the queued jobs.
    assert await redis_client.get(monthly_key) == "3"


async def test_monthly_budget_cap(db_session, spy_redis, redis_client, monkeypatch):
    monkeypatch.setattr(settings, "duffel_daily_search_budget", 100)
    monkeypatch.setattr(settings, "duffel_monthly_search_budget", 5)
    daily_key, monthly_key = budget_keys()
    # Monthly counter already near its limit (3 of 5 used).
    await redis_client.set(monthly_key, 3)

    for i in range(5):
        await make_target(db_session, destination=f"D{i}")

    await schedule_due_polls({"redis": spy_redis})

    # Only the remaining monthly allowance (5 - 3 = 2) is queued.
    assert len(spy_redis.enqueued) == 2
    assert await redis_client.get(monthly_key) == "5"
    assert await redis_client.get(daily_key) == "2"


async def test_user_tracked_priority(db_session, spy_redis, monkeypatch):
    monkeypatch.setattr(settings, "duffel_daily_search_budget", 2)
    monkeypatch.setattr(settings, "duffel_monthly_search_budget", 100)
    # Insert coverage targets first to prove ordering is by column, not insertion.
    await make_target(db_session, is_user_tracked=False, destination="C0")
    await make_target(db_session, is_user_tracked=False, destination="C1")
    u1 = await make_target(db_session, is_user_tracked=True, destination="U0")
    u2 = await make_target(db_session, is_user_tracked=True, destination="U1")

    await schedule_due_polls({"redis": spy_redis})

    queued_ids = {args[0] for _, args in spy_redis.enqueued}
    assert len(spy_redis.enqueued) == 2
    assert queued_ids == {u1.id, u2.id}


async def test_counter_expiry_set_on_first_create(
    db_session, spy_redis, redis_client, monkeypatch
):
    monkeypatch.setattr(settings, "duffel_daily_search_budget", 100)
    monkeypatch.setattr(settings, "duffel_monthly_search_budget", 100)
    await make_target(db_session)

    await schedule_due_polls({"redis": spy_redis})

    daily_key, monthly_key = budget_keys()
    daily_ttl = await redis_client.ttl(daily_key)
    monthly_ttl = await redis_client.ttl(monthly_key)
    # Freshly created keys carry the configured expiries.
    assert DAILY_TTL - 60 < daily_ttl <= DAILY_TTL
    assert MONTHLY_TTL - 60 < monthly_ttl <= MONTHLY_TTL


async def test_no_due_targets(db_session, spy_redis, redis_client, monkeypatch):
    monkeypatch.setattr(settings, "duffel_daily_search_budget", 100)
    monkeypatch.setattr(settings, "duffel_monthly_search_budget", 100)
    await make_target(db_session, due=False)

    await schedule_due_polls({"redis": spy_redis})

    daily_key, monthly_key = budget_keys()
    assert spy_redis.enqueued == []
    # No counters were touched.
    assert await redis_client.get(daily_key) is None
    assert await redis_client.get(monthly_key) is None


@pytest.mark.parametrize("limit_field", ["daily", "monthly"])
async def test_counters_never_left_above_limit(
    db_session, spy_redis, redis_client, monkeypatch, limit_field
):
    if limit_field == "daily":
        monkeypatch.setattr(settings, "duffel_daily_search_budget", 2)
        monkeypatch.setattr(settings, "duffel_monthly_search_budget", 100)
    else:
        monkeypatch.setattr(settings, "duffel_daily_search_budget", 100)
        monkeypatch.setattr(settings, "duffel_monthly_search_budget", 2)
    for i in range(5):
        await make_target(db_session, destination=f"D{i}")

    await schedule_due_polls({"redis": spy_redis})

    daily_key, monthly_key = budget_keys()
    assert int(await redis_client.get(daily_key)) <= settings.duffel_daily_search_budget
    assert (
        int(await redis_client.get(monthly_key))
        <= settings.duffel_monthly_search_budget
    )
