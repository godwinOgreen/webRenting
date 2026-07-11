# app/domains/search/models.py

"""
Domain: Search
Tables: search_history, saved_searches, favorites

Three models that handle search-related functionality.

SearchHistory — tracks what users search for (analytics + personalization)
SavedSearch   — saved search criteria with alert functionality (Celery-driven)
Favorite      — properties a user has bookmarked for later viewing

Why Search owns these models (not users or properties):
  These three models exist because of the search flow. Without search:
    - SearchHistory has no purpose (it IS search activity)
    - SavedSearch has no purpose (it IS a saved search query)
    - Favorite is the outlier — it's more of a "user saves property" action.
      But it lives here because:
      1. Favorites are typically accessed from the search results page
      2. The favorite action happens in the search/property-detail context
      3. Grouping all "user saves stuff" in one domain is cleaner than splitting

SearchHistory is anonymous-friendly:
  user_id is nullable. Guests can search without logging in.
  Logged-in users get personalized search history.
  Guest search data is still valuable for analytics (trending locations, etc.).

SavedSearch alert flow (Celery: saved_search_alerts.py):
  1. User saves a search with criteria (city, price range, property type, etc.)
  2. SavedSearch created: is_active=True
  3. Every 6 hours, Celery runs saved_search_alerts task:
     a. For each active saved search:
        - Build query from saved criteria
        - Filter: is_publicly_visible=True AND created_at > last_notified_at
        - If matches found:
          → Send notification to user
          → Update last_notified_at = now()
     b. Uses SAVED_SEARCH_ALERT_INTERVAL_HOURS from constants.py
  4. User deactivates: is_active=False → Celery skips this search

Favorite uniqueness:
  UNIQUE (user_id, property_id) — a user cannot favorite the same property twice.
  Enforced at database level. Service catches the duplicate and returns existing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Integer,
    Numeric, String, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.hybrid import hybrid_property

from app.db.base import Base
from app.db.mixins import UUIDMixin, CreatedAtMixin
from app.domains.properties.models import PropertyType

if TYPE_CHECKING:
    from app.domains.properties.models import Property
    from app.domains.users.models import User


# ─── SearchHistory Model ─────────────────────────────────────────────────────

class SearchHistory(Base, UUIDMixin, CreatedAtMixin):
    """
    A record of a single search query performed by a user (or guest).

    Table: search_history

    user_id is nullable:
      Logged-in users: user_id set, search linked to their profile.
      Guests: user_id NULL, search tracked by session_id for analytics.

    property_id is nullable:
      Set when the user clicks a property FROM the search results.
      NULL for the search query itself (no property clicked yet).
      This allows tracking "search → click" conversion rates.

    Uses CreatedAtMixin (not TimestampMixin):
      Search history is immutable. A search happened at a specific time.
      No updates needed.

    Analytics value:
      - Trending locations: GROUP BY location, COUNT(*), ORDER BY count DESC
      - Popular price ranges: AVG(min_price), AVG(max_price)
      - Search-to-click conversion: COUNT(property_id) / COUNT(*)
    """

    __tablename__ = "search_history"

    # ── Who searched (nullable for guests) ────────────────────────────────────
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment=(
            "The user who performed this search. NULL for anonymous guests. "
            "SET NULL: user deleted → search history survives for analytics. "
            "Indexed: 'get search history for user X' queries."
        ),
    )

    # ── Search criteria ───────────────────────────────────────────────────────
    location: Mapped[Optional[str]] = mapped_column(
        String(300), nullable=True,
        comment=(
            "The location string the user searched for. "
            "e.g. 'Lekki Phase 1', 'Victoria Island', 'Ikoyi'. "
            "Stored as entered by user. Normalized for analytics in a future task."
        ),
    )

    # ── Property clicked from results (nullable) ─────────────────────────────
    property_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="SET NULL"),
        nullable=True,
        comment=(
            "The property the user clicked from search results. "
            "NULL for the search query itself (no click yet). "
            "SET NULL: property deleted → search history survives. "
            "Used for search-to-click conversion analytics."
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[Optional[User]] = relationship(
        "User",
        back_populates="search_history",
    )

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<SearchHistory id={self.id} "
            f"user_id={self.user_id} "
            f"location={self.location!r}>"
        )


# ─── SavedSearch Model ───────────────────────────────────────────────────────

class SavedSearch(Base, UUIDMixin, CreatedAtMixin):
    """
    A saved search query with alert functionality.

    Table: saved_searches

    Users save search criteria to receive notifications when new matching
    properties are published. Celery saved_search_alerts task checks every
    SAVED_SEARCH_ALERT_INTERVAL_HOURS (6 hours).

    Alert matching logic (in saved_search_alerts task):
      For each active saved search, build a query:
        SELECT * FROM properties
        WHERE is_publicly_visible = True
          AND created_at > saved_search.last_notified_at
          AND (city = saved_search.city OR saved_search.city IS NULL)
          AND (state = saved_search.state OR saved_search.state IS NULL)
          AND (price >= saved_search.min_price OR saved_search.min_price IS NULL)
          AND (price <= saved_search.max_price OR saved_search.max_price IS NULL)
          AND (property_type = saved_search.property_type OR saved_search.property_type IS NULL)
          AND (bedrooms >= saved_search.min_bedrooms OR saved_search.min_bedrooms IS NULL)
      If matches found → notification sent → last_notified_at updated.

    NULL criteria means "any":
      city=NULL means "any city"
      min_price=NULL means "any price"
      property_type=NULL means "any type"
      This allows broad saved searches (e.g. "any property in Lagos under 500k")

    Uses CreatedAtMixin (not TimestampMixin):
      No updated_at needed. is_active and last_notified_at are operational
      changes, not content edits. The model tracks alert activity via
      last_notified_at directly.
    """

    __tablename__ = "saved_searches"

    # ── Who saved this search ─────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment=(
            "The user who saved this search. "
            "CASCADE: user deleted → their saved searches are disposable. "
            "Indexed: 'get all saved searches for user X' queries."
        ),
    )

    # ── Optional name ─────────────────────────────────────────────────────────
    name: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True,
        comment=(
            "User-given name for this saved search. "
            "e.g. 'Lekki 3-bed under 500k', 'VI apartments'. "
            "NULL if user didn't name it. Shown in the saved searches list."
        ),
    )

    # ── Search criteria (NULL = any) ──────────────────────────────────────────
    city: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True,
        comment="Filter by city. NULL = any city.",
    )
    state: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True,
        comment="Filter by state. NULL = any state.",
    )
    min_price: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 2), nullable=True,
        comment="Minimum price filter. NULL = no minimum.",
    )
    max_price: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(15, 2), nullable=True,
        comment="Maximum price filter. NULL = no maximum.",
    )
    property_type: Mapped[Optional[PropertyType]] = mapped_column(
        sa.Enum(
            PropertyType,
            name="property_type",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=True,
        comment="Filter by property type. NULL = any type.",
    )
    min_bedrooms: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
        comment="Minimum number of bedrooms. NULL = any.",
    )

    # ── Alert status ──────────────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False,
        server_default=text("true"),
        default=True,
        index=True,
        comment=(
            "True = Celery checks this search for new matches. "
            "False = user paused alerts. Celery skips inactive searches. "
            "Indexed: Celery queries WHERE is_active=True."
        ),
    )
    last_notified_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        index=True,
        comment=(
            "When the last alert notification was sent. "
            "NULL = never notified (alert everything matching since creation). "
            "Celery updates this after sending an alert. "
            "Used as the lower bound for 'new matches' query: "
            "WHERE property.created_at > saved_search.last_notified_at."
        ),
    )

    # ── Constraints ───────────────────────────────────────────────────────────
    __table_args__ = (
        CheckConstraint(
            "min_price IS NULL OR min_price >= 0",
            name="chk_saved_search_min_price",
        ),
        CheckConstraint(
            "max_price IS NULL OR max_price >= 0",
            name="chk_saved_search_max_price",
        ),
        CheckConstraint(
            "(min_price IS NULL OR max_price IS NULL) OR (max_price >= min_price)",
            name="chk_saved_search_price_range",
        ),
        CheckConstraint(
            "min_bedrooms IS NULL OR min_bedrooms >= 0",
            name="chk_saved_search_bedrooms",
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        back_populates="saved_searches",
    )

    # ── Computed properties: hybrid (usable in queries) ───────────────────────

    @hybrid_property
    def is_alerting(self) -> bool:
        """
        True if this saved search is actively checking for new matches.
        Combines is_active flag with "has criteria" check.
        A saved search with zero criteria matches everything — not useful for alerts.
        """
        return (
            self.is_active
            and (
                self.city is not None
                or self.state is not None
                or self.min_price is not None
                or self.max_price is not None
                or self.property_type is not None
                or self.min_bedrooms is not None
            )
        )

    @is_alerting.expression
    def is_alerting(cls):
        """
        SQL: WHERE SavedSearch.is_alerting

        Used by Celery saved_search_alerts:
            stmt = select(SavedSearch).where(SavedSearch.is_alerting)
        """
        return sa.and_(
            cls.is_active == True,  # noqa: E712
            sa.or_(
                cls.city.isnot(None),
                cls.state.isnot(None),
                cls.min_price.isnot(None),
                cls.max_price.isnot(None),
                cls.property_type.isnot(None),
                cls.min_bedrooms.isnot(None),
            ),
        )

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def has_been_notified(self) -> bool:
        """True if at least one alert has been sent for this saved search."""
        return self.last_notified_at is not None

    @property
    def summary(self) -> str:
        """
        Human-readable summary of the saved search criteria.
        Used in notification messages and the saved searches list UI.

        Examples:
          "3+ bed in Lekki, ₦200k–₦500k"
          "Any property in Lagos"
          "Apartment in VI, max ₦300k"
        """
        parts = []

        # Bedrooms
        if self.min_bedrooms is not None:
            parts.append(f"{self.min_bedrooms}+ bed")

        # Location
        location_parts = [p for p in [self.city, self.state] if p]
        if location_parts:
            parts.append("in " + ", ".join(location_parts))

        # Price range
        if self.min_price is not None and self.max_price is not None:
            parts.append(f"₦{self.min_price:,.0f}–₦{self.max_price:,.0f}")
        elif self.max_price is not None:
            parts.append(f"max ₦{self.max_price:,.0f}")
        elif self.min_price is not None:
            parts.append(f"from ₦{self.min_price:,.0f}")

        # Property type
        if self.property_type is not None:
            parts.append(self.property_type.value.title())

        return ", ".join(parts) if parts else "All properties"

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<SavedSearch id={self.id} "
            f"user_id={self.user_id} "
            f"name={self.name!r} "
            f"active={self.is_active}>"
        )


# ─── Favorite Model ──────────────────────────────────────────────────────────

class Favorite(Base, UUIDMixin, CreatedAtMixin):
    """
    A property bookmarked by a user for later viewing.

    Table: favorites

    UNIQUE (user_id, property_id):
      A user cannot favorite the same property twice.
      Service catches duplicates and returns the existing favorite.

    Uses CreatedAtMixin (not TimestampMixin):
      Favorites are never updated. Only created and deleted.
      created_at is when the user bookmarked the property.

    Cascade behavior:
      user_id → CASCADE: user deleted → favorites are disposable
      property_id → CASCADE: property deleted → favorite link is meaningless
      Both are correct because favorites are disposable links, not permanent records.
    """

    __tablename__ = "favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "property_id", name="uq_user_favorite"),
    )

    # ── Who favorited ─────────────────────────────────────────────────────────
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment=(
            "The user who bookmarked this property. "
            "CASCADE: user deleted → favorites are disposable. "
            "Indexed: 'get all favorites for user X' queries (most common)."
        ),
    )

    # ── What was favorited ────────────────────────────────────────────────────
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment=(
            "The bookmarked property. "
            "CASCADE: property deleted → favorite link is meaningless. "
            "Indexed: 'get all users who favorited property X' queries. "
            "Part of UNIQUE constraint (user_id, property_id)."
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    user: Mapped[User] = relationship(
        "User",
        back_populates="favorites",
    )
    listing: Mapped[Property] = relationship(
        "Property",
        back_populates="favorites",
    )

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<Favorite id={self.id} "
            f"user_id={self.user_id} "
            f"property_id={self.property_id}>"
        )
