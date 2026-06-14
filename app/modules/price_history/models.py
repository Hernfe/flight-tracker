from datetime import date, datetime

from sqlalchemy import BigInteger, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    origin: Mapped[str]
    destination: Mapped[str]
    departure_date: Mapped[date]
    return_date: Mapped[date | None]
    cabin: Mapped[str]
    source: Mapped[str]
    cheapest_price_cents: Mapped[int]
    mean_top_n_price_cents: Mapped[int | None]
    n_offers: Mapped[int]
    currency: Mapped[str]
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index(
            "ix_price_history_route_observed",
            "origin",
            "destination",
            "departure_date",
            "observed_at",
        ),
    )