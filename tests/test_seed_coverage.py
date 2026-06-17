from datetime import date, timedelta

from sqlalchemy import func, select

from app.modules.flights.models import PollTarget
from scripts.seed_coverage import OFFSETS_DAYS, ROUTES, seed_coverage

EXPECTED = len(ROUTES) * len(OFFSETS_DAYS)


async def count_targets(session) -> int:
    return await session.scalar(select(func.count()).select_from(PollTarget))


async def test_first_run_creates_expected_targets(db_session):
    created = await seed_coverage(db_session)
    assert created == EXPECTED
    assert await count_targets(db_session) == EXPECTED
    # All seeded targets are coverage (non-user-tracked) targets.
    user_tracked = await db_session.scalar(
        select(func.count())
        .select_from(PollTarget)
        .where(PollTarget.is_user_tracked.is_(True))
    )
    assert user_tracked == 0


async def test_second_run_creates_no_duplicates(db_session):
    first = await seed_coverage(db_session)
    second = await seed_coverage(db_session)
    assert first == EXPECTED
    assert second == 0
    assert await count_targets(db_session) == EXPECTED


async def test_existing_user_tracked_does_not_block_coverage(db_session):
    # A user-tracked target on a route/date that coverage also wants must not
    # be treated as the non-user-tracked duplicate.
    origin, destination = ROUTES[0]
    departure_date = date.today() + timedelta(days=OFFSETS_DAYS[0])
    db_session.add(
        PollTarget(
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            is_user_tracked=True,
        )
    )
    await db_session.commit()

    created = await seed_coverage(db_session)

    # Coverage still creates its full set; the user-tracked row is separate.
    assert created == EXPECTED
    assert await count_targets(db_session) == EXPECTED + 1
    coverage_match = await db_session.scalar(
        select(func.count())
        .select_from(PollTarget)
        .where(
            PollTarget.origin == origin,
            PollTarget.destination == destination,
            PollTarget.departure_date == departure_date,
            PollTarget.is_user_tracked.is_(False),
        )
    )
    assert coverage_match == 1
