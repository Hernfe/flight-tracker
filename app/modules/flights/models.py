import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, SmallInteger, func
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
    is_user_tracked: Mapped[bool] = mapped_column(default=False)
    wishlist_item_id: Mapped[uuid.UUID | None]
    active: Mapped[bool] = mapped_column(default=True)
    next_poll_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
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
