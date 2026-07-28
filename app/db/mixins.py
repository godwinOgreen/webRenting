"""
Reusable SQLAlchemy column mixins (SQLAlchemy 2.0).

UUIDMixin          → UUID primary key
CreatedAtMixin     → created_at only
UpdatedAtMixin     → updated_at only
TimestampMixin     → created_at + updated_at

Usage:
    class User(Base, UUIDMixin, TimestampMixin):         # id + created_at + updated_at
    class Favorite(Base, UUIDMixin, CreatedAtMixin):     # id + created_at
    class Payment(Base, UUIDMixin, UpdatedAtMixin):      # id + updated_at
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


class UUIDMixin:
    """UUID primary key with PostgreSQL gen_random_uuid()."""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )


class CreatedAtMixin:
    """created_at — set once on insert."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class UpdatedAtMixin:
    """
    updated_at — auto-updated by DB trigger (created in Alembic migration).
    onupdate=func.now() is the Python-side fallback.
    """

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TimestampMixin(CreatedAtMixin, UpdatedAtMixin):
    """created_at + updated_at."""

    pass
