"""Unified poll-target layer.

Collection and tracking are not two pollers — they are two *reasons* a
(origin, destination, departure_date, return_date, cabin) target is active. The
helpers here own the reference counting: adding a reason creates or revives a
single deduplicated target, removing the last reason deactivates it. Cadence is
one function of days-to-departure for every target, with a single tightening
for tracked targets very close to departure.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.flights.models import PollTarget, PollTargetReason

# Airlines sell roughly 11–12 months out; never seed targets past this.
SELL_HORIZON_DAYS = 330


def poll_interval(days_to_departure: int, *, has_tracking: bool) -> timedelta:
    """One cadence for every target. Identical for collection and tracking,
    except a tracked target under 3 days out is tightened from 12h to 6h."""
    d = days_to_departure
    if d >= 180:
        return timedelta(days=14)
    if d >= 60:
        return timedelta(days=7)
    if d >= 14:
        return timedelta(days=1)
    if d >= 3:
        return timedelta(days=1)
    # under 3 days
    return timedelta(hours=6) if has_tracking else timedelta(hours=12)


def _key_clause(origin, destination, departure_date, return_date, cabin):
    clause = [
        PollTarget.origin == origin,
        PollTarget.destination == destination,
        PollTarget.departure_date == departure_date,
        PollTarget.cabin == cabin,
    ]
    # return_date is nullable; NULL must match NULL.
    if return_date is None:
        clause.append(PollTarget.return_date.is_(None))
    else:
        clause.append(PollTarget.return_date == return_date)
    return clause


async def get_or_create_target(
    session: AsyncSession,
    *,
    origin: str,
    destination: str,
    departure_date: date,
    return_date: date | None,
    cabin: str,
    source: str = "duffel",
) -> PollTarget:
    """Return the single target for this key, creating it (inactive) if absent.

    The target is created inactive; activation is the job of whichever reason is
    added next, so a target never lingers active without a reason.
    """
    existing = await session.scalar(
        select(PollTarget).where(
            *_key_clause(origin, destination, departure_date, return_date, cabin)
        )
    )
    if existing is not None:
        return existing

    target = PollTarget(
        origin=origin,
        destination=destination,
        departure_date=departure_date,
        return_date=return_date,
        cabin=cabin,
        source=source,
        active=False,
        next_poll_at=datetime.now(timezone.utc),
    )
    session.add(target)
    await session.flush()
    return target


async def has_tracking_reason(session: AsyncSession, target_id: int) -> bool:
    return bool(
        await session.scalar(
            select(
                exists().where(
                    PollTargetReason.poll_target_id == target_id,
                    PollTargetReason.reason_type == "tracking",
                )
            )
        )
    )


async def add_collection_reason(session: AsyncSession, target: PollTarget) -> None:
    present = await session.scalar(
        select(
            exists().where(
                PollTargetReason.poll_target_id == target.id,
                PollTargetReason.reason_type == "collection",
            )
        )
    )
    if not present:
        session.add(
            PollTargetReason(poll_target_id=target.id, reason_type="collection")
        )
    target.active = True


async def add_tracking_reason(
    session: AsyncSession, target: PollTarget, wishlist_item_id: uuid.UUID
) -> None:
    present = await session.scalar(
        select(
            exists().where(
                PollTargetReason.poll_target_id == target.id,
                PollTargetReason.reason_type == "tracking",
                PollTargetReason.wishlist_item_id == wishlist_item_id,
            )
        )
    )
    if not present:
        session.add(
            PollTargetReason(
                poll_target_id=target.id,
                reason_type="tracking",
                wishlist_item_id=wishlist_item_id,
            )
        )
    target.active = True


async def _deactivate_if_empty(session: AsyncSession, target_id: int) -> None:
    still_has = await session.scalar(
        select(exists().where(PollTargetReason.poll_target_id == target_id))
    )
    if not still_has:
        target = await session.get(PollTarget, target_id)
        if target is not None:
            target.active = False


async def remove_tracking_reasons_for_item(
    session: AsyncSession, wishlist_item_id: uuid.UUID
) -> None:
    """Drop one item's tracking reason from every target it touched, then
    deactivate any target left with no reasons."""
    affected = list(
        await session.scalars(
            select(PollTargetReason.poll_target_id).where(
                PollTargetReason.wishlist_item_id == wishlist_item_id
            )
        )
    )
    await session.execute(
        delete(PollTargetReason).where(
            PollTargetReason.wishlist_item_id == wishlist_item_id
        )
    )
    await session.flush()
    for target_id in set(affected):
        await _deactivate_if_empty(session, target_id)


async def remove_tracking_reasons_for_item_except(
    session: AsyncSession,
    wishlist_item_id: uuid.UUID,
    keep_target_ids: set[int],
) -> None:
    """Prune an item's tracking reasons from targets it no longer cares about
    (used when a wishlist item's dates change), keeping the desired set."""
    # notin_ over an empty collection is a no-op match, so fall back to a
    # sentinel that matches nothing real, dropping every reason for the item.
    keep = list(keep_target_ids) if keep_target_ids else [0]
    affected = list(
        await session.scalars(
            select(PollTargetReason.poll_target_id).where(
                PollTargetReason.wishlist_item_id == wishlist_item_id,
                PollTargetReason.poll_target_id.notin_(keep),
            )
        )
    )
    await session.execute(
        delete(PollTargetReason).where(
            PollTargetReason.wishlist_item_id == wishlist_item_id,
            PollTargetReason.poll_target_id.notin_(keep),
        )
    )
    await session.flush()
    for target_id in set(affected):
        await _deactivate_if_empty(session, target_id)


async def deactivate_passed_and_empty(
    session: AsyncSession, today: date | None = None
) -> None:
    """Mark inactive any active target whose departure has passed or which has
    no reasons left. The due poller then skips them."""
    today = today or date.today()
    targets = list(
        await session.scalars(select(PollTarget).where(PollTarget.active.is_(True)))
    )
    for target in targets:
        if target.departure_date < today:
            target.active = False
            continue
        has_reason = await session.scalar(
            select(exists().where(PollTargetReason.poll_target_id == target.id))
        )
        if not has_reason:
            target.active = False
