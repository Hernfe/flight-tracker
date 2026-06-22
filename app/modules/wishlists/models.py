"""Minimal wishlists schema needed by the poll layer.

Identity comes from Supabase Auth, so every user reference is a bare UUID equal
to the Supabase auth user id — there is no local users table and no FK to one.
Membership (and therefore which users a tracking reason fans out to) lives in
``WishlistMember``; a wishlist item never carries a user id directly.

This is intentionally the smallest real schema Task 2 needs. CRUD endpoints,
the auth flow, and the static airport reference load are out of scope here.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

DATE_MODES = ("fixed", "flexible")
MEMBER_ROLES = ("owner", "editor", "viewer")


class Wishlist(Base):
    __tablename__ = "wishlists"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID]
    name: Mapped[str]
    is_shared: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WishlistMember(Base):
    __tablename__ = "wishlist_members"

    wishlist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wishlists.id", ondelete="CASCADE"), primary_key=True
    )
    # Supabase auth user id.
    user_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    role: Mapped[str]

    __table_args__ = (
        CheckConstraint(
            "role in ('owner', 'editor', 'viewer')",
            name="ck_wishlist_members_role",
        ),
    )


class WishlistItem(Base):
    __tablename__ = "wishlist_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    wishlist_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("wishlists.id", ondelete="CASCADE"), index=True
    )
    origin: Mapped[str]
    destination: Mapped[str]
    cabin: Mapped[str] = mapped_column(default="economy", server_default="economy")

    # 'fixed' -> departure_date (+ optional return_date).
    # 'flexible' -> [window_start, window_end] (+ optional trip-length bounds).
    date_mode: Mapped[str]
    departure_date: Mapped[date | None]
    return_date: Mapped[date | None]
    window_start: Mapped[date | None]
    window_end: Mapped[date | None]
    trip_length_min_days: Mapped[int | None]
    trip_length_max_days: Mapped[int | None]

    budget_threshold_cents: Mapped[int]
    currency: Mapped[str] = mapped_column(default="EUR", server_default="EUR")
    active: Mapped[bool] = mapped_column(default=True, server_default="true")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(origin) = 3 and char_length(destination) = 3",
            name="ck_wishlist_items_iata_len",
        ),
        CheckConstraint(
            "date_mode in ('fixed', 'flexible')",
            name="ck_wishlist_items_date_mode",
        ),
        # Fixed and flexible date fields must never be mixed.
        CheckConstraint(
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
        CheckConstraint(
            "window_start is null or window_end is null or window_end >= window_start",
            name="ck_wishlist_items_window_order",
        ),
        CheckConstraint(
            "trip_length_min_days is null"
            " or trip_length_max_days is null"
            " or trip_length_max_days >= trip_length_min_days",
            name="ck_wishlist_items_trip_length_order",
        ),
    )
