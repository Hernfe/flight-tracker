from datetime import date, timedelta

from sqlalchemy import func, select

from app.modules.flights.models import PollTarget, PollTargetReason
from app.modules.flights.poll import (
    add_collection_reason,
    add_tracking_reason,
    deactivate_passed_and_empty,
    get_or_create_target,
    poll_interval,
    remove_tracking_reasons_for_item,
)
from app.modules.wishlists.models import Wishlist, WishlistItem


def test_poll_interval_bands():
    assert poll_interval(200, has_tracking=False) == timedelta(days=14)
    assert poll_interval(180, has_tracking=False) == timedelta(days=14)
    assert poll_interval(120, has_tracking=False) == timedelta(days=7)
    assert poll_interval(60, has_tracking=False) == timedelta(days=7)
    assert poll_interval(30, has_tracking=False) == timedelta(days=1)
    assert poll_interval(14, has_tracking=False) == timedelta(days=1)
    assert poll_interval(5, has_tracking=False) == timedelta(days=1)
    assert poll_interval(3, has_tracking=False) == timedelta(days=1)
    # under 3 days: collection 12h, tracking tightened to 6h.
    assert poll_interval(2, has_tracking=False) == timedelta(hours=12)
    assert poll_interval(2, has_tracking=True) == timedelta(hours=6)
    assert poll_interval(0, has_tracking=True) == timedelta(hours=6)


async def _make_item(session) -> WishlistItem:
    import uuid

    wl = Wishlist(id=uuid.uuid4(), owner_id=uuid.uuid4(), name="t")
    session.add(wl)
    await session.flush()
    item = WishlistItem(
        id=uuid.uuid4(),
        wishlist_id=wl.id,
        origin="HEL",
        destination="LHR",
        date_mode="fixed",
        departure_date=date.today() + timedelta(days=30),
        budget_threshold_cents=20000,
    )
    session.add(item)
    await session.flush()
    return item


async def test_collection_and_tracking_share_one_target(db_session):
    dep = date.today() + timedelta(days=30)
    item = await _make_item(db_session)

    t1 = await get_or_create_target(
        db_session,
        origin="HEL",
        destination="LHR",
        departure_date=dep,
        return_date=None,
        cabin="economy",
    )
    await add_collection_reason(db_session, t1)

    # A tracking reason on the same key reuses the same target, no duplicate.
    t2 = await get_or_create_target(
        db_session,
        origin="HEL",
        destination="LHR",
        departure_date=dep,
        return_date=None,
        cabin="economy",
    )
    await add_tracking_reason(db_session, t2, item.id)
    await db_session.commit()

    assert t1.id == t2.id
    assert await db_session.scalar(select(func.count()).select_from(PollTarget)) == 1
    reasons = await db_session.scalar(
        select(func.count())
        .select_from(PollTargetReason)
        .where(PollTargetReason.poll_target_id == t1.id)
    )
    assert reasons == 2


async def test_reasons_are_idempotent(db_session):
    dep = date.today() + timedelta(days=30)
    item = await _make_item(db_session)
    target = await get_or_create_target(
        db_session,
        origin="HEL",
        destination="LHR",
        departure_date=dep,
        return_date=None,
        cabin="economy",
    )
    await add_collection_reason(db_session, target)
    await add_collection_reason(db_session, target)
    await add_tracking_reason(db_session, target, item.id)
    await add_tracking_reason(db_session, target, item.id)
    await db_session.commit()

    total = await db_session.scalar(
        select(func.count())
        .select_from(PollTargetReason)
        .where(PollTargetReason.poll_target_id == target.id)
    )
    assert total == 2


async def test_removing_last_reason_deactivates(db_session):
    dep = date.today() + timedelta(days=30)
    item = await _make_item(db_session)
    target = await get_or_create_target(
        db_session,
        origin="HEL",
        destination="LHR",
        departure_date=dep,
        return_date=None,
        cabin="economy",
    )
    await add_tracking_reason(db_session, target, item.id)
    await db_session.commit()
    assert target.active is True

    await remove_tracking_reasons_for_item(db_session, item.id)
    await db_session.commit()
    await db_session.refresh(target)
    assert target.active is False


async def test_collection_reason_survives_tracking_removal(db_session):
    dep = date.today() + timedelta(days=30)
    item = await _make_item(db_session)
    target = await get_or_create_target(
        db_session,
        origin="HEL",
        destination="LHR",
        departure_date=dep,
        return_date=None,
        cabin="economy",
    )
    await add_collection_reason(db_session, target)
    await add_tracking_reason(db_session, target, item.id)
    await db_session.commit()

    await remove_tracking_reasons_for_item(db_session, item.id)
    await db_session.commit()
    await db_session.refresh(target)
    # Collection reason remains, so the target stays active.
    assert target.active is True


async def test_deactivate_passed_departures(db_session):
    past = await get_or_create_target(
        db_session,
        origin="HEL",
        destination="CDG",
        departure_date=date.today() - timedelta(days=1),
        return_date=None,
        cabin="economy",
    )
    await add_collection_reason(db_session, past)
    await db_session.commit()

    await deactivate_passed_and_empty(db_session)
    await db_session.commit()
    await db_session.refresh(past)
    assert past.active is False
