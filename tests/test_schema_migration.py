"""Prove the test database schema is the one produced by the Alembic chain.

These assertions read the live information_schema of appdb_test (which conftest
builds via `alembic upgrade head`), so they fail if the migration that replaced
the single-reason poll_targets columns with poll_target_reasons is missing.
"""

from sqlalchemy import text


async def _table_columns(session, table: str) -> set[str]:
    rows = await session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :t"
        ),
        {"t": table},
    )
    return {row[0] for row in rows}


async def test_migrated_schema_has_poll_target_reasons(db_session):
    columns = await _table_columns(db_session, "poll_target_reasons")
    assert columns, "poll_target_reasons table is missing from the migrated schema"
    assert {"poll_target_id", "reason_type", "wishlist_item_id"} <= columns


async def test_poll_targets_dropped_legacy_columns(db_session):
    columns = await _table_columns(db_session, "poll_targets")
    assert columns, "poll_targets table is missing from the migrated schema"
    assert "is_user_tracked" not in columns
    assert "wishlist_item_id" not in columns
