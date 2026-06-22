"""In-process typed event bus.

A deliberately narrow boundary between modules. The normalizer publishes
``PriceBucketUpdated`` after it has durably written a price-history bucket; the
(future) alerts module subscribes and evaluates active wishlist items. The
publisher never learns who is listening, so the normalizer stays free of
wishlist/user/alert concerns.
"""

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceBucketUpdated:
    """Emitted once per (stops, cabin) price-history bucket that was written."""

    poll_target_id: int | None
    origin: str
    destination: str
    departure_date: date
    return_date: date | None
    cabin: str
    stops_bucket: int
    price_cents: int
    currency: str
    carrier_code: str | None
    observed_at: datetime


Subscriber = Callable[[PriceBucketUpdated], Awaitable[None] | None]

_subscribers: list[Subscriber] = []


def subscribe(fn: Subscriber) -> Subscriber:
    """Register a subscriber. Idempotent so re-imports don't double-register."""
    if fn not in _subscribers:
        _subscribers.append(fn)
    return fn


def unsubscribe(fn: Subscriber) -> None:
    if fn in _subscribers:
        _subscribers.remove(fn)


def clear_subscribers() -> None:
    """Test helper: drop every subscriber."""
    _subscribers.clear()


async def publish(event: PriceBucketUpdated) -> None:
    """Deliver ``event`` to all subscribers.

    A subscriber raising must never affect the publisher (the normalizer), so
    exceptions are logged and swallowed.
    """
    for fn in list(_subscribers):
        try:
            result = fn(event)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("event subscriber %r failed for %r", fn, event)
