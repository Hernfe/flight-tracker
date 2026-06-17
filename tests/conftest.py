import asyncio
from datetime import datetime, timezone

import asyncpg
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.modules.flights.models import PollTarget

TEST_DB_NAME = "appdb_test"

_url = make_url(settings.database_url)
_test_url = _url.set(database=TEST_DB_NAME)


async def _create_test_database() -> None:
    conn = await asyncpg.connect(
        user=_url.username,
        password=_url.password,
        host=_url.host,
        port=_url.port,
        database="postgres",
    )
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB_NAME
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await conn.close()


async def _create_schema() -> None:
    engine = create_async_engine(_test_url.render_as_string(hide_password=False))
    async with engine.begin() as conn:
        await conn.run_sync(PollTarget.__table__.create, checkfirst=True)
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_db():
    asyncio.run(_create_test_database())
    asyncio.run(_create_schema())


@pytest_asyncio.fixture
async def test_engine():
    engine = create_async_engine(_test_url.render_as_string(hide_password=False))
    # Start each test from a clean poll_targets table.
    async with engine.begin() as conn:
        await conn.exec_driver_sql("TRUNCATE TABLE poll_targets RESTART IDENTITY")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(test_engine, monkeypatch):
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    # Point the worker tasks at the test database.
    monkeypatch.setattr("app.workers.tasks.SessionLocal", factory)
    return factory


@pytest_asyncio.fixture
async def db_session(session_factory):
    async with session_factory() as session:
        yield session


class SpyRedis:
    """Wraps a real redis client so counter semantics (incr/expire/decr/ttl)
    are exercised for real, while enqueue_job calls are recorded."""

    def __init__(self, client: aioredis.Redis):
        self._client = client
        self.enqueued: list[tuple[str, tuple]] = []

    async def incr(self, key: str) -> int:
        return await self._client.incr(key)

    async def decr(self, key: str) -> int:
        return await self._client.decr(key)

    async def expire(self, key: str, seconds: int) -> bool:
        return await self._client.expire(key, seconds)

    async def get(self, key: str):
        return await self._client.get(key)

    async def ttl(self, key: str) -> int:
        return await self._client.ttl(key)

    async def enqueue_job(self, name: str, *args) -> None:
        self.enqueued.append((name, args))


def budget_keys() -> tuple[str, str]:
    today = datetime.now(timezone.utc).date()
    return (
        f"duffel:searches:{today.isoformat()}",
        f"duffel:searches:{today.strftime('%Y-%m')}",
    )


@pytest_asyncio.fixture
async def redis_client():
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    daily_key, monthly_key = budget_keys()
    await client.delete(daily_key, monthly_key)
    yield client
    await client.delete(daily_key, monthly_key)
    await client.aclose()


@pytest_asyncio.fixture
async def spy_redis(redis_client):
    return SpyRedis(redis_client)
