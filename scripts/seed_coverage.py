import asyncio
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, engine
from app.modules.flights.models import PollTarget

ROUTES = [
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

OFFSETS_DAYS = [14, 30, 60, 90]


async def seed_coverage(session: AsyncSession) -> int:
    today = date.today()
    created = 0
    for origin, destination in ROUTES:
        for offset in OFFSETS_DAYS:
            departure_date = today + timedelta(days=offset)
            existing = await session.scalar(
                select(PollTarget.id).where(
                    PollTarget.origin == origin,
                    PollTarget.destination == destination,
                    PollTarget.departure_date == departure_date,
                    PollTarget.is_user_tracked.is_(False),
                )
            )
            if existing is not None:
                continue
            session.add(
                PollTarget(
                    origin=origin,
                    destination=destination,
                    departure_date=departure_date,
                    is_user_tracked=False,
                )
            )
            created += 1
    await session.commit()
    return created


async def main() -> None:
    async with SessionLocal() as session:
        created = await seed_coverage(session)
    print(f"Created {created} coverage targets")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
