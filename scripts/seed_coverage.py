import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, engine
from app.modules.flights.coverage import maintain_collection_coverage
from app.modules.flights.models import PollTarget


async def seed_coverage(session: AsyncSession) -> int:
    """Ensure the rolling collection coverage targets exist. Returns the total
    number of active poll targets afterwards."""
    await maintain_collection_coverage(session)
    await session.commit()
    return await session.scalar(
        select(func.count()).select_from(PollTarget).where(PollTarget.active.is_(True))
    )


async def main() -> None:
    async with SessionLocal() as session:
        total = await seed_coverage(session)
    print(f"Active coverage targets: {total}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
