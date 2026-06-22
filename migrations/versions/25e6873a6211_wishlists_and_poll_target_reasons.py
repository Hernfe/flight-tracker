"""wishlists, poll_target_reasons, reference-counted poll targets

Revision ID: 25e6873a6211
Revises: c74eae0dbb6e
Create Date: 2026-06-22 00:10:00.000000

Hand-written (no local DB to run autogenerate against). Mirrors the models in
app.modules.wishlists.models and app.modules.flights.models. Replaces the old
single-reason poll_targets columns (is_user_tracked, wishlist_item_id) with the
reference-counted poll_target_reasons table.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "25e6873a6211"
down_revision: Union[str, Sequence[str], None] = "c74eae0dbb6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # --- wishlists ---------------------------------------------------------
    op.create_table(
        "wishlists",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "is_shared",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "wishlist_members",
        sa.Column("wishlist_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.CheckConstraint(
            "role in ('owner', 'editor', 'viewer')",
            name="ck_wishlist_members_role",
        ),
        sa.ForeignKeyConstraint(["wishlist_id"], ["wishlists.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("wishlist_id", "user_id"),
    )
    op.create_table(
        "wishlist_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("wishlist_id", sa.Uuid(), nullable=False),
        sa.Column("origin", sa.String(), nullable=False),
        sa.Column("destination", sa.String(), nullable=False),
        sa.Column("cabin", sa.String(), server_default="economy", nullable=False),
        sa.Column("date_mode", sa.String(), nullable=False),
        sa.Column("departure_date", sa.Date(), nullable=True),
        sa.Column("return_date", sa.Date(), nullable=True),
        sa.Column("window_start", sa.Date(), nullable=True),
        sa.Column("window_end", sa.Date(), nullable=True),
        sa.Column("trip_length_min_days", sa.Integer(), nullable=True),
        sa.Column("trip_length_max_days", sa.Integer(), nullable=True),
        sa.Column("budget_threshold_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), server_default="EUR", nullable=False),
        sa.Column(
            "active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(origin) = 3 and char_length(destination) = 3",
            name="ck_wishlist_items_iata_len",
        ),
        sa.CheckConstraint(
            "date_mode in ('fixed', 'flexible')",
            name="ck_wishlist_items_date_mode",
        ),
        sa.CheckConstraint(
            "("
            "  date_mode = 'fixed'"
            "  and departure_date is not null"
            "  and window_start is null and window_end is null"
            ") or ("
            "  date_mode = 'flexible'"
            "  and window_start is not null and window_end is not null"
            "  and departure_date is null and return_date is null"
            ")",
            name="ck_wishlist_items_date_fields",
        ),
        sa.CheckConstraint(
            "window_start is null or window_end is null or window_end >= window_start",
            name="ck_wishlist_items_window_order",
        ),
        sa.CheckConstraint(
            "trip_length_min_days is null"
            " or trip_length_max_days is null"
            " or trip_length_max_days >= trip_length_min_days",
            name="ck_wishlist_items_trip_length_order",
        ),
        sa.ForeignKeyConstraint(["wishlist_id"], ["wishlists.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_wishlist_items_wishlist_id",
        "wishlist_items",
        ["wishlist_id"],
        unique=False,
    )

    # --- poll_target_reasons ----------------------------------------------
    op.create_table(
        "poll_target_reasons",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("poll_target_id", sa.Integer(), nullable=False),
        sa.Column("reason_type", sa.String(), nullable=False),
        sa.Column("wishlist_item_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "reason_type in ('collection', 'tracking')",
            name="ck_poll_target_reasons_type",
        ),
        sa.CheckConstraint(
            "(reason_type = 'tracking') = (wishlist_item_id is not null)",
            name="ck_poll_target_reasons_item",
        ),
        sa.ForeignKeyConstraint(
            ["poll_target_id"], ["poll_targets.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["wishlist_item_id"], ["wishlist_items.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_poll_target_reasons_poll_target_id",
        "poll_target_reasons",
        ["poll_target_id"],
        unique=False,
    )
    op.create_index(
        "uq_poll_target_reasons_collection",
        "poll_target_reasons",
        ["poll_target_id"],
        unique=True,
        postgresql_where=sa.text("reason_type = 'collection'"),
    )
    op.create_index(
        "uq_poll_target_reasons_tracking",
        "poll_target_reasons",
        ["poll_target_id", "wishlist_item_id"],
        unique=True,
        postgresql_where=sa.text("reason_type = 'tracking'"),
    )

    # Preserve existing targets: every current poll_target was coverage, so give
    # each a collection reason before the old columns disappear.
    op.execute(
        "INSERT INTO poll_target_reasons (poll_target_id, reason_type) "
        "SELECT id, 'collection' FROM poll_targets"
    )

    # --- poll_targets: drop the old single-reason columns, add key index ----
    op.create_index(
        "ix_poll_targets_key",
        "poll_targets",
        ["origin", "destination", "departure_date", "return_date", "cabin"],
        unique=False,
    )
    op.drop_column("poll_targets", "is_user_tracked")
    op.drop_column("poll_targets", "wishlist_item_id")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "poll_targets",
        sa.Column("wishlist_item_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "poll_targets",
        sa.Column(
            "is_user_tracked",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.drop_index("ix_poll_targets_key", table_name="poll_targets")

    op.drop_index("uq_poll_target_reasons_tracking", table_name="poll_target_reasons")
    op.drop_index("uq_poll_target_reasons_collection", table_name="poll_target_reasons")
    op.drop_index(
        "ix_poll_target_reasons_poll_target_id",
        table_name="poll_target_reasons",
    )
    op.drop_table("poll_target_reasons")

    op.drop_index("ix_wishlist_items_wishlist_id", table_name="wishlist_items")
    op.drop_table("wishlist_items")
    op.drop_table("wishlist_members")
    op.drop_table("wishlists")
