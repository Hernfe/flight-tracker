"""Coverage maintainer: decides which dates become targets.

This is the *only* difference between collection and tracking — which dates get
a reason, not how often a target is polled. Collection keeps a rolling sampled
spread of dates across the next 180 days for the top HEL routes; tracking mirrors
the dates each active wishlist item cares about.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.flights.poll import (
    add_collection_reason,
    deactivate_passed_and_empty,
    get_or_create_target,
)
from app.modules.wishlists.models import WishlistItem
from app.modules.wishlists.service import sync_wishlist_item

# Top HEL routes seed list. Trimmed to what exists today; extend toward the top
# 100 as the static destination reference lands.
ROUTES: list[tuple[str, str]] = [
    ("HEL", "LHR"),
    ("HEL", "CDG"),
    ("HEL", "AMS"),
    ("HEL", "BER"),
    ("HEL", "ARN"),
    ("HEL", "CPH"),
    ("HEL", "FRA"),
    ("HEL", "BCN"),
    ("HEL", "MAD"),
    ("HEL", "FCO"),
    ("HEL", "VIE"),
    ("HEL", "PRG"),
    ("HEL", "JFK"),
    ("HEL", "BKK"),
    ("HEL", "DXB"),
]

# Sample one date every 30 days across the 0–180 day window.
COLLECTION_STEP_DAYS = 30
COLLECTION_HORIZON_DAYS = 180
COLLECTION_CABIN = "economy"


def collection_grid(
    today: date,
    step: int = COLLECTION_STEP_DAYS,
    horizon: int = COLLECTION_HORIZON_DAYS,
) -> list[date]:
    """Dates on an absolute ordinal grid within [today, today + horizon].

    Anchoring to the ordinal (not to ``today``) keeps the covered set stable as
    the window slides: a date drops off the front only when it passes, and a new
    one appears near the 180-day edge, so coverage stays roughly constant.
    """
    start = today.toordinal()
    first = start + (-start % step)
    return [date.fromordinal(o) for o in range(first, start + horizon + 1, step)]


async def maintain_collection_coverage(
    session: AsyncSession, today: date | None = None
) -> None:
    today = today or date.today()
    for origin, destination in ROUTES:
        for departure_date in collection_grid(today):
            target = await get_or_create_target(
                session,
                origin=origin,
                destination=destination,
                departure_date=departure_date,
                return_date=None,
                cabin=COLLECTION_CABIN,
            )
            await add_collection_reason(session, target)


async def maintain_tracking_coverage(
    session: AsyncSession, today: date | None = None
) -> None:
    today = today or date.today()
    items = list(
        await session.scalars(select(WishlistItem).where(WishlistItem.active.is_(True)))
    )
    for item in items:
        await sync_wishlist_item(session, item, today=today)


async def run_maintainer(session: AsyncSession, today: date | None = None) -> None:
    """Daily pass: refresh collection + tracking coverage, then sweep stale
    targets (passed departure dates or no remaining reasons)."""
    today = today or date.today()
    await maintain_collection_coverage(session, today)
    await maintain_tracking_coverage(session, today)
    await deactivate_passed_and_empty(session, today)
