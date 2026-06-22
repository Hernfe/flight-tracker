import asyncio
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio
import redis.asyncio as aioredis
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

# Import every model module so the ORM mappings used by the tests are loaded.
import app.modules.destinations.models  # noqa: F401
import app.modules.flights.models  # noqa: F401
import app.modules.price_history.models  # noqa: F401
import app.modules.wishlists.models  # noqa: F401

TEST_DB_NAME = "appdb_test"

_url = make_url(settings.database_url)
_test_url = _url.set(database=TEST_DB_NAME)
_test_url_str = _test_url.render_as_string(hide_password=False)

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _BACKEND_ROOT / "alembic.ini"


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


async def _reset_schema() -> None:
    # Drop everything so `alembic upgrade head` runs against a clean database,
    # even if a previous test session left tables (and alembic_version) behind.
    engine = create_async_engine(_test_url_str)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("DROP SCHEMA public CASCADE")
        await conn.exec_driver_sql("CREATE SCHEMA public")
    await engine.dispose()


def _migrate_to_head() -> None:
    # Build the test schema from the Alembic migration chain (the same chain
    # production and development use). The test database URL is passed
    # explicitly through the config's attributes so env.py targets appdb_test
    # without modifying the user's .env / app settings.
    alembic_cfg = Config(str(_ALEMBIC_INI))
    alembic_cfg.attributes["sqlalchemy.url"] = _test_url_str
    command.upgrade(alembic_cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def _ensure_test_db():
    asyncio.run(_create_test_database())
    asyncio.run(_reset_schema())
    _migrate_to_head()


@pytest_asyncio.fixture
async def test_engine():
    engine = create_async_engine(_test_url.render_as_string(hide_password=False))
    # Start each test from clean poll/wishlist tables. CASCADE clears the FK
    # children (poll_target_reasons, wishlist_items, ...) in one shot.
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "TRUNCATE TABLE poll_targets, poll_target_reasons, "
            "wishlist_items, wishlist_members, wishlists "
            "RESTART IDENTITY CASCADE"
        )
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
