"""Service boundary between wishlist items and the unified poll layer.

Creating, changing, or activating a wishlist item adds/updates its tracking
reason on deduplicated poll targets; deactivating or deleting it removes those
reasons. A route already covered by collection gains a tracking reason on the
*same* target rather than a duplicate, because everything routes through
``get_or_create_target``.
"""

import re
import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.flights.poll import (
    SELL_HORIZON_DAYS,
    add_tracking_reason,
    deactivate_passed_and_empty,
    get_or_create_target,
    remove_tracking_reasons_for_item,
    remove_tracking_reasons_for_item_except,
)
from app.modules.wishlists.models import WishlistItem

_IATA_RE = re.compile(r"^[A-Z]{3}$")


def normalize_iata(code: str) -> str:
    """Uppercase/trim an IATA code and reject anything that isn't 3 letters."""
    normalized = code.strip().upper()
    if not _IATA_RE.match(normalized):
        raise ValueError(f"invalid IATA code: {code!r}")
    return normalized


def item_target_dates(
    item: WishlistItem, today: date
) -> list[tuple[date, date | None]]:
    """The (departure, return) pairs an item wants polled, clamped to what
    airlines currently sell and never in the past."""
    horizon = today + timedelta(days=SELL_HORIZON_DAYS)

    if item.date_mode == "fixed":
        dep = item.departure_date
        if dep is None or dep < today or dep > horizon:
            return []
        return [(dep, item.return_date)]

    # flexible: every departure day across the window, with a representative
    # return derived from the minimum trip length (if any). Narrowing the window
    # by the user's free calendar slots is a later refinement.
    start = max(item.window_start, today)
    end = min(item.window_end, horizon)
    pairs: list[tuple[date, date | None]] = []
    day = start
    while day <= end:
        ret = (
            day + timedelta(days=item.trip_length_min_days)
            if item.trip_length_min_days is not None
            else None
        )
        pairs.append((day, ret))
        day += timedelta(days=1)
    return pairs


async def sync_wishlist_item(
    session: AsyncSession, item: WishlistItem, today: date | None = None
) -> None:
    """Reconcile a wishlist item's tracking reasons with its current dates.

    Idempotent: safe to call on create, on any edit, and on activation toggles.
    """
    today = today or date.today()

    if not item.active:
        await remove_tracking_reasons_for_item(session, item.id)
        await deactivate_passed_and_empty(session, today)
        return

    origin = normalize_iata(item.origin)
    destination = normalize_iata(item.destination)

    desired_target_ids: set[int] = set()
    for departure_date, return_date in item_target_dates(item, today):
        target = await get_or_create_target(
            session,
            origin=origin,
            destination=destination,
            departure_date=departure_date,
            return_date=return_date,
            cabin=item.cabin,
        )
        await add_tracking_reason(session, target, item.id)
        desired_target_ids.add(target.id)

    # Drop tracking reasons on targets this item no longer cares about (e.g. its
    # dates moved), deactivating any target left with no reasons.
    await remove_tracking_reasons_for_item_except(session, item.id, desired_target_ids)


async def on_wishlist_item_deleted(
    session: AsyncSession, wishlist_item_id: uuid.UUID, today: date | None = None
) -> None:
    await remove_tracking_reasons_for_item(session, wishlist_item_id)
    await deactivate_passed_and_empty(session, today or date.today())
