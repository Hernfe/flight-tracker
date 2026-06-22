import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.modules.flights.coverage import maintain_collection_coverage
from app.modules.flights.models import PollTarget, PollTargetReason
from app.modules.flights.poll import add_collection_reason, get_or_create_target
from app.modules.wishlists.models import Wishlist, WishlistItem
from app.modules.wishlists.service import (
    item_target_dates,
    normalize_iata,
    on_wishlist_item_deleted,
    sync_wishlist_item,
)


async def _wishlist(session) -> Wishlist:
    wl = Wishlist(id=uuid.uuid4(), owner_id=uuid.uuid4(), name="trips")
    session.add(wl)
    await session.flush()
    return wl


def test_normalize_iata():
    assert normalize_iata(" hel ") == "HEL"
    with pytest.raises(ValueError):
        normalize_iata("HELS")


def test_item_target_dates_fixed():
    today = date(2026, 6, 22)
    item = WishlistItem(
        id=uuid.uuid4(),
        wishlist_id=uuid.uuid4(),
        origin="HEL",
        destination="LHR",
        date_mode="fixed",
        departure_date=date(2026, 7, 10),
        return_date=date(2026, 7, 17),
        budget_threshold_cents=20000,
    )
    assert item_target_dates(item, today) == [(date(2026, 7, 10), date(2026, 7, 17))]


def test_item_target_dates_flexible_with_trip_length():
    today = date(2026, 6, 22)
    item = WishlistItem(
        id=uuid.uuid4(),
        wishlist_id=uuid.uuid4(),
        origin="HEL",
        destination="LHR",
        date_mode="flexible",
        window_start=date(2026, 7, 1),
        window_end=date(2026, 7, 3),
        trip_length_min_days=5,
        budget_threshold_cents=20000,
    )
    assert item_target_dates(item, today) == [
        (date(2026, 7, 1), date(2026, 7, 6)),
        (date(2026, 7, 2), date(2026, 7, 7)),
        (date(2026, 7, 3), date(2026, 7, 8)),
    ]


async def test_sync_creates_tracking_targets(db_session):
    wl = await _wishlist(db_session)
    item = WishlistItem(
        id=uuid.uuid4(),
        wishlist_id=wl.id,
        origin="hel",
        destination="cdg",
        date_mode="fixed",
        departure_date=date.today() + timedelta(days=40),
        budget_threshold_cents=15000,
    )
    db_session.add(item)
    await db_session.flush()

    await sync_wishlist_item(db_session, item)
    await db_session.commit()

    target = await db_session.scalar(select(PollTarget))
    assert target is not None
    assert target.origin == "HEL" and target.destination == "CDG"
    assert target.active is True
    tracking = await db_session.scalar(
        select(func.count())
        .select_from(PollTargetReason)
        .where(
            PollTargetReason.reason_type == "tracking",
            PollTargetReason.wishlist_item_id == item.id,
        )
    )
    assert tracking == 1


async def test_collected_route_gains_tracking_not_duplicate(db_session):
    dep = date.today() + timedelta(days=30)
    # Pre-existing collection target on the exact route/date.
    existing = await get_or_create_target(
        db_session,
        origin="HEL",
        destination="LHR",
        departure_date=dep,
        return_date=None,
        cabin="economy",
    )
    await add_collection_reason(db_session, existing)
    await db_session.commit()

    wl = await _wishlist(db_session)
    item = WishlistItem(
        id=uuid.uuid4(),
        wishlist_id=wl.id,
        origin="HEL",
        destination="LHR",
        date_mode="fixed",
        departure_date=dep,
        budget_threshold_cents=15000,
    )
    db_session.add(item)
    await db_session.flush()

    await sync_wishlist_item(db_session, item)
    await db_session.commit()

    # Still exactly one target, now carrying both reasons.
    assert await db_session.scalar(select(func.count()).select_from(PollTarget)) == 1
    reasons = await db_session.scalar(
        select(func.count())
        .select_from(PollTargetReason)
        .where(PollTargetReason.poll_target_id == existing.id)
    )
    assert reasons == 2


async def test_delete_item_removes_tracking_and_deactivates(db_session):
    wl = await _wishlist(db_session)
    item = WishlistItem(
        id=uuid.uuid4(),
        wishlist_id=wl.id,
        origin="HEL",
        destination="AMS",
        date_mode="fixed",
        departure_date=date.today() + timedelta(days=50),
        budget_threshold_cents=15000,
    )
    db_session.add(item)
    await db_session.flush()
    await sync_wishlist_item(db_session, item)
    await db_session.commit()

    target = await db_session.scalar(select(PollTarget))
    await on_wishlist_item_deleted(db_session, item.id)
    await db_session.commit()
    await db_session.refresh(target)

    assert target.active is False
    remaining = await db_session.scalar(
        select(func.count()).select_from(PollTargetReason)
    )
    assert remaining == 0


async def test_sync_prunes_stale_dates_after_change(db_session):
    wl = await _wishlist(db_session)
    item = WishlistItem(
        id=uuid.uuid4(),
        wishlist_id=wl.id,
        origin="HEL",
        destination="BCN",
        date_mode="fixed",
        departure_date=date.today() + timedelta(days=60),
        budget_threshold_cents=15000,
    )
    db_session.add(item)
    await db_session.flush()
    await sync_wishlist_item(db_session, item)
    await db_session.commit()

    # The user moves the date; re-sync must drop the old target's tracking reason.
    item.departure_date = date.today() + timedelta(days=70)
    await sync_wishlist_item(db_session, item)
    await db_session.commit()

    tracking = await db_session.scalar(
        select(func.count())
        .select_from(PollTargetReason)
        .where(PollTargetReason.wishlist_item_id == item.id)
    )
    assert tracking == 1
    # The originally-seeded target is now reasonless and inactive.
    active = await db_session.scalar(
        select(func.count()).select_from(PollTarget).where(PollTarget.active.is_(True))
    )
    assert active == 1


async def test_maintainer_collection_then_tracking(db_session):
    # Collection coverage plus a tracking item dedupe through the same layer.
    today = date.today()
    await maintain_collection_coverage(db_session, today)
    await db_session.commit()
    base = await db_session.scalar(select(func.count()).select_from(PollTarget))

    wl = await _wishlist(db_session)
    item = WishlistItem(
        id=uuid.uuid4(),
        wishlist_id=wl.id,
        origin="HEL",
        destination="LHR",
        date_mode="fixed",
        departure_date=today + timedelta(days=400),  # beyond sell horizon
        budget_threshold_cents=15000,
    )
    db_session.add(item)
    await db_session.flush()
    await sync_wishlist_item(db_session, item)
    await db_session.commit()

    # Date beyond the airline sell horizon seeds nothing.
    assert await db_session.scalar(select(func.count()).select_from(PollTarget)) == base
