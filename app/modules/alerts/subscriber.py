"""TEMPORARY STUB for the future alerts module.

This is the listening half of the event boundary. The real alerts module will
replace ``handle_price_bucket_updated`` with logic that loads active
``WishlistItem`` rows whose tracking reason is attached to the event's poll
target, checks whether each owner's ``budget_threshold_cents`` is now crossed,
and dispatches notifications. For now we only prove the boundary is wired and
defer evaluation, writing a structured debug log.

Do not add delivery channels, persistence, retries, or preferences here — that
belongs to the real alerts module.
"""

import logging

from app.core.events import PriceBucketUpdated, subscribe

logger = logging.getLogger(__name__)


async def handle_price_bucket_updated(event: PriceBucketUpdated) -> None:
    # STUB: real alert evaluation is deferred to the alerts module.
    logger.debug(
        "alert evaluation deferred for price bucket",
        extra={
            "event": "price_bucket_updated",
            "poll_target_id": event.poll_target_id,
            "origin": event.origin,
            "destination": event.destination,
            "departure_date": event.departure_date.isoformat(),
            "return_date": event.return_date.isoformat() if event.return_date else None,
            "cabin": event.cabin,
            "stops_bucket": event.stops_bucket,
            "price_cents": event.price_cents,
            "currency": event.currency,
            "carrier_code": event.carrier_code,
        },
    )


def register() -> None:
    """Attach the placeholder subscriber to the event bus."""
    subscribe(handle_price_bucket_updated)
