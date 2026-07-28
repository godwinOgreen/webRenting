# app/domains/analytics/models.py

"""
Domain: Analytics
Tables: property_analytics
No enums (event_type is String — validated in analytics_service)

One model that captures raw analytics events for property interactions.

PropertyAnalytics — a single analytics event (view, contact, booking, etc.)

Why raw events instead of aggregated counts:
  Storing each event as a row allows flexible aggregation:
    - "Views this week" → COUNT(*) WHERE event_type='view' AND created_at > ...
    - "Views by day"    → GROUP BY DATE(created_at)
    - "Conversion rate" → COUNT(contact) / COUNT(view)
    - "Top referrers"   → GROUP BY metadata->>'referrer'

  Aggregated counts (property.view_count = 42) lose this flexibility.
  If you need "views this month by mobile users" — you can't answer that
  from a single count column.

Analytics flow:
  1. User views a property → analytics_service.track_event()
  2. INSERT into property_analytics (one row per event)
  3. Celery daily_analytics task (runs at 00:30 UTC daily):
     a. Aggregates raw events into summary metrics
     b. Sends daily digest to agents: "Your listing got 45 views yesterday"
  4. Agent dashboard queries raw events for interactive charts

event_type values (validated in analytics_service):
  view            — property detail page loaded
  contact         — "Contact Agent" button clicked
  booking         — booking created from this property
  favorite        — property added to favorites
  phone_reveal    — phone number revealed by user
  share           — property link shared (WhatsApp, copy link, etc.)
  search_impression — property appeared in search results

  Stored as String (not enum) for flexibility.
  Adding a new event type (e.g. "virtual_tour_viewed") requires no migration.

Why event_metadata is JSONB:
  Different event types carry different context:
    view:            {"referrer": "search_results", "position": 5, "device": "mobile"}
    contact:         {"method": "message", "search_query": "lekki 3 bed"}
    share:           {"platform": "whatsapp"}
    search_impression: {"search_query": "...", "page": 2, "position": 15}
  JSONB stores any structure without schema changes.
  Queryable: WHERE event_metadata->>'referrer' = 'search_results'

user_id is nullable:
  Guest views are valuable analytics. A guest viewing a property counts as
  a view even without a user account. session_id tracks guest activity.
  Logged-in events have both user_id and session_id.

NDPR compliance:
  Raw IP addresses are NOT stored. If geolocation is needed, it's derived
  from the request at query time, not persisted.
  session_id is a random identifier, not PII.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import CreatedAtMixin, UUIDMixin

if TYPE_CHECKING:
    from app.domains.properties.models import Property
    from app.domains.users.models import User


# ─── Model ───────────────────────────────────────────────────────────────────


class PropertyAnalytics(Base, UUIDMixin, CreatedAtMixin):
    """
    A single analytics event for a property interaction.

    Table: property_analytics

    High-volume table: every property view, click, and interaction is a row.
    Designed for fast INSERTs and flexible aggregation queries.

    Uses CreatedAtMixin (not TimestampMixin):
      Analytics events are immutable. They record what happened at a specific
      time. No updates needed. created_at IS the event timestamp.

    Query patterns:
      Agent dashboard:  WHERE property_id = ? AND created_at > ? GROUP BY event_type
      Daily digest:     WHERE property_id = ? AND created_at::date = yesterday() GROUP BY event_type
      Conversion rate:  COUNT(contact) / COUNT(view) for property_id = ?
      Trending:         GROUP BY property_id ORDER BY COUNT(*) DESC LIMIT 10
    """

    __tablename__ = "property_analytics"

    # ── What property ─────────────────────────────────────────────────────────
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment=(
            "The property this event is about. "
            "CASCADE: property deleted → its analytics are disposable. "
            "Indexed: almost every query filters by property_id. "
            "Part of composite index (property_id, created_at) for dashboard queries."
        ),
    )

    # ── Who triggered the event (nullable for guests) ─────────────────────────
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment=(
            "The user who triggered this event. NULL for anonymous guests. "
            "SET NULL: user deleted → analytics survive for aggregation. "
            "Indexed: 'get all activity by user X' queries."
        ),
    )

    # ── What happened ─────────────────────────────────────────────────────────
    event_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment=(
            "Type of interaction. Valid values (validated in analytics_service): "
            "view | contact | booking | favorite | phone_reveal | share | search_impression. "
            "Stored as String (not enum) for flexibility — adding new types requires no migration."
        ),
    )

    # ── Session tracking ──────────────────────────────────────────────────────
    session_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        comment=(
            "Frontend session identifier for guest activity tracking. "
            "Generated by frontend (localStorage UUID or cookie). "
            "NULL if session tracking unavailable. "
            "Allows grouping guest events into sessions for funnel analysis. "
            "Not PII — random identifier, not tied to personal data."
        ),
    )

    # ── Flexible event context ────────────────────────────────────────────────
    event_metadata: Mapped[dict | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=(
            "Flexible context data. Structure varies by event_type: "
            "view:            {'referrer': 'search_results', 'position': 5, 'device': 'mobile'} "
            "contact:         {'method': 'message', 'search_query': 'lekki 3 bed'} "
            "share:           {'platform': 'whatsapp'} "
            "search_impression: {'search_query': '...', 'page': 2, 'position': 15} "
            "Queryable: WHERE event_metadata->>'referrer' = 'search_results'."
        ),
    )

    # ── Table constraints and indexes ─────────────────────────────────────────
    __table_args__ = (
        # Composite index for the most common dashboard query:
        # "show me analytics for property X in the last 30 days"
        # SELECT event_type, COUNT(*) FROM property_analytics
        # WHERE property_id = ? AND created_at > now() - '30 days'
        # GROUP BY event_type
        Index(
            "idx_analytics_property_created",
            "property_id",
            "created_at",
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    listing: Mapped[Property] = relationship(
        "Property",
        back_populates="analytics",
    )
    user: Mapped[User | None] = relationship(
        "User",
        back_populates="analytics_events",
    )

    # ── Computed properties: hybrid (usable in queries) ───────────────────────

    @hybrid_property
    def is_view(self) -> bool:
        return self.event_type == "view"

    @is_view.expression
    def is_view(cls):
        """
        SQL: WHERE PropertyAnalytics.is_view

        Used in view count query:
            stmt = select(func.count()).where(
                PropertyAnalytics.property_id == property_id,
                PropertyAnalytics.is_view,
                PropertyAnalytics.created_at > cutoff_date,
            )
        """
        return cls.event_type == "view"

    @hybrid_property
    def is_contact(self) -> bool:
        return self.event_type == "contact"

    @is_contact.expression
    def is_contact(cls):
        """SQL: WHERE PropertyAnalytics.is_contact"""
        return cls.event_type == "contact"

    @hybrid_property
    def is_from_search(self) -> bool:
        """
        True if this event originated from search results.
        Checks event_metadata for referrer context.

        NOTE: This is a Python-only property. The SQL expression version
        uses JSONB operator for database-level filtering.
        """
        if self.event_metadata is None:
            return False
        return self.event_metadata.get("referrer") == "search_results"

    @is_from_search.expression
    def is_from_search(cls):
        """
        SQL: WHERE PropertyAnalytics.is_from_search

        Uses PostgreSQL JSONB containment operator.
        Filters events that originated from search results.
        Used for search-to-view conversion analytics.
        """
        return cls.event_metadata["referrer"].astext == "search_results"

    # ── Computed properties: display-only ─────────────────────────────────────

    @property
    def is_guest_event(self) -> bool:
        """True if this event was triggered by an anonymous guest."""
        return self.user_id is None

    @property
    def device_type(self) -> str | None:
        """
        Extracts device type from event_metadata.
        Returns 'mobile', 'tablet', 'desktop', or None.
        """
        if self.event_metadata is None:
            return None
        return self.event_metadata.get("device")

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<PropertyAnalytics id={self.id} "
            f"property_id={self.property_id} "
            f"event={self.event_type!r} "
            f"user_id={self.user_id}>"
        )
