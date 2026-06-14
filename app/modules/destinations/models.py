from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Destination(Base):
    __tablename__ = "destinations"

    id: Mapped[int] = mapped_column(primary_key=True)
    iata_code: Mapped[str] = mapped_column(unique=True, index=True)
    city_name: Mapped[str | None]
    country_code: Mapped[str | None]
    lat: Mapped[float | None]
    lng: Mapped[float | None]
    timezone: Mapped[str | None]