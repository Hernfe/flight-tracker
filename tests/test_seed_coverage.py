from datetime import date

from sqlalchemy import func, select

from app.modules.flights.coverage import (
    ROUTES,
    collection_grid,
    maintain_collection_coverage,
)
from app.modules.flights.models import PollTarget, PollTargetReason


async def count_targets(session) -> int:
    return await session.scalar(select(func.count()).select_from(PollTarget))


async def count_collection_reasons(session) -> int:
    return await session.scalar(
        select(func.count())
        .select_from(PollTargetReason)
        .where(PollTargetReason.reason_type == "collection")
    )


def test_collection_grid_within_window_and_anchored():
    today = date(2026, 6, 22)
    grid = collection_grid(today, step=30, horizon=180)
    assert grid == [date.fromordinal(o) for o in grid]
    # Every date is on the absolute ordinal grid and inside the window.
    assert all(d.toordinal() % 30 == 0 for d in grid)
    assert all(today <= d <= date(2026, 12, 19) for d in grid)
    assert grid == sorted(grid)


async def test_collection_creates_targets_and_reasons(db_session):
    today = date.today()
    await maintain_collection_coverage(db_session, today)
    await db_session.commit()

    expected = len(ROUTES) * len(collection_grid(today))
    assert await count_targets(db_session) == expected
    assert await count_collection_reasons(db_session) == expected

    # Every coverage target is active.
    active = await db_session.scalar(
        select(func.count()).select_from(PollTarget).where(PollTarget.active.is_(True))
    )
    assert active == expected


async def test_collection_is_idempotent(db_session):
    today = date.today()
    await maintain_collection_coverage(db_session, today)
    await db_session.commit()
    first = await count_targets(db_session)

    await maintain_collection_coverage(db_session, today)
    await db_session.commit()

    assert await count_targets(db_session) == first
    assert await count_collection_reasons(db_session) == first
