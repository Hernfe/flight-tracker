import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PollTarget(Base):
    __tablename__ = "poll_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    origin: Mapped[str]
    destination: Mapped[str]
    departure_date: Mapped[date]
    return_date: Mapped[date | None]
    cabin: Mapped[str] = mapped_column(default="economy")
    source: Mapped[str] = mapped_column(default="duffel")
    # active mirrors "has at least one reason"; maintained by the poll service.
    active: Mapped[bool] = mapped_column(default=True)
    next_poll_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        # A target is keyed by (origin, destination, departure_date, return_date,
        # cabin); this index backs the get-or-create dedup lookup.
        Index(
            "ix_poll_targets_key",
            "origin",
            "destination",
            "departure_date",
            "return_date",
            "cabin",
        ),
    )


class PollTargetReason(Base):
    """Reference-counted reason a target is active.

    Either the standing "collection" reason or a "tracking" reason that points
    at a wishlist item (and through its members, at users). A route that is both
    collected and tracked is one target carrying several reasons, polled once.
    """

    __tablename__ = "poll_target_reasons"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    poll_target_id: Mapped[int] = mapped_column(
        ForeignKey("poll_targets.id", ondelete="CASCADE"), index=True
    )
    reason_type: Mapped[str]  # 'collection' | 'tracking'
    wishlist_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("wishlist_items.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "reason_type in ('collection', 'tracking')",
            name="ck_poll_target_reasons_type",
        ),
        # tracking reasons reference an item; collection reasons never do.
        CheckConstraint(
            "(reason_type = 'tracking') = (wishlist_item_id is not null)",
            name="ck_poll_target_reasons_item",
        ),
        # At most one collection reason per target.
        Index(
            "uq_poll_target_reasons_collection",
            "poll_target_id",
            unique=True,
            postgresql_where=text("reason_type = 'collection'"),
        ),
        # At most one tracking reason per (target, wishlist item).
        Index(
            "uq_poll_target_reasons_tracking",
            "poll_target_id",
            "wishlist_item_id",
            unique=True,
            postgresql_where=text("reason_type = 'tracking'"),
        ),
    )


class ApiResponseRaw(Base):
    __tablename__ = "api_responses_raw"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source: Mapped[str]
    request_params: Mapped[dict] = mapped_column(JSONB)
    response_body: Mapped[dict] = mapped_column(JSONB)
    http_status: Mapped[int]
    poll_target_id: Mapped[int | None] = mapped_column(ForeignKey("poll_targets.id"))
    request_id: Mapped[str]
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FlightOffer(Base):
    __tablename__ = "flight_offers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    raw_response_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("api_responses_raw.id")
    )
    origin: Mapped[str]
    destination: Mapped[str]
    departure_date: Mapped[date]
    return_date: Mapped[date | None]
    cabin: Mapped[str] = mapped_column(server_default="economy")
    stops: Mapped[int] = mapped_column(SmallInteger)
    carrier: Mapped[str | None]
    fare_brand: Mapped[str | None]
    price_cents: Mapped[int]
    currency: Mapped[str]
    source: Mapped[str]
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_flight_offers_route_observed",
            "origin",
            "destination",
            "departure_date",
            "observed_at",
        ),
    )
