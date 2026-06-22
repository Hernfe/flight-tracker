"""price_history stops/carrier, smallint stops, rebuilt index

Revision ID: c74eae0dbb6e
Revises: 499d75f87b51
Create Date: 2026-06-22 00:00:00.000000

Hand-written (no local DB to run autogenerate against). Mirrors the column and
index definitions in app.modules.price_history.models and app.modules.flights.models.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c74eae0dbb6e"
down_revision: Union[str, Sequence[str], None] = "499d75f87b51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # price_history: per (stops, cabin) bucket with the winning carrier.
    op.add_column("price_history", sa.Column("stops", sa.SmallInteger(), nullable=True))
    op.add_column("price_history", sa.Column("carrier", sa.String(), nullable=True))
    op.alter_column(
        "price_history",
        "cabin",
        existing_type=sa.String(),
        server_default=sa.text("'economy'"),
        existing_nullable=False,
    )
    # Backfill existing rows: cabin -> 'economy'; stops and carrier stay null.
    op.execute("UPDATE price_history SET cabin = 'economy' WHERE cabin IS NULL")

    # Swap the route index for one that includes the bucket columns.
    op.drop_index("ix_price_history_route_observed", table_name="price_history")
    op.create_index(
        "ix_price_history_route_stops_cabin_observed",
        "price_history",
        ["origin", "destination", "departure_date", "stops", "cabin", "observed_at"],
        unique=False,
    )

    # flight_offers: stops as smallint, cabin default 'economy'.
    op.alter_column(
        "flight_offers",
        "stops",
        existing_type=sa.Integer(),
        type_=sa.SmallInteger(),
        existing_nullable=False,
    )
    op.alter_column(
        "flight_offers",
        "cabin",
        existing_type=sa.String(),
        server_default=sa.text("'economy'"),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "flight_offers",
        "cabin",
        existing_type=sa.String(),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "flight_offers",
        "stops",
        existing_type=sa.SmallInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )

    op.drop_index(
        "ix_price_history_route_stops_cabin_observed", table_name="price_history"
    )
    op.create_index(
        "ix_price_history_route_observed",
        "price_history",
        ["origin", "destination", "departure_date", "observed_at"],
        unique=False,
    )

    op.alter_column(
        "price_history",
        "cabin",
        existing_type=sa.String(),
        server_default=None,
        existing_nullable=False,
    )
    op.drop_column("price_history", "carrier")
    op.drop_column("price_history", "stops")
