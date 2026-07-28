# app/db/base.py

"""
SQLAlchemy declarative base and PostgreSQL event listeners.

Base is the foundation class for all 27 models.
Every model inherits from Base (directly or via mixins).

PostgreSQL event listeners:
  1. Creates uuid-ossp extension (for UUID generation)
  2. Creates update_updated_at_column() trigger function
  3. Creates updated_at triggers on all tables that have an updated_at column

These listeners fire when Base.metadata.create_all() is called
(e.g. in tests). For production, extensions and triggers are created
in the initial Alembic migration.

No `from __future__ import annotations` — causes SQLAlchemy type
resolution errors with some annotation styles.
"""

from sqlalchemy import event, text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


# ── PostgreSQL: uuid-ossp extension ──────────────────────────────────────────
# Provides uuid_generate_v4() and other UUID functions.
# gen_random_uuid() (used in our UUIDMixin) is built-in for PostgreSQL 13+
# but the extension is idempotent and useful to have.


@event.listens_for(Base.metadata, "after_create")
def _create_extensions(target, connection, **kwargs):
    connection.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'))


# ── PostgreSQL: updated_at trigger function ──────────────────────────────────
# Shared trigger function for all tables with an updated_at column.
# Sets updated_at = NOW() on every UPDATE automatically.
# One function, many triggers — one per table that needs it.

_TRIGGER_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER_SQL = """
CREATE TRIGGER set_updated_at
    BEFORE UPDATE ON {table_name}
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
"""


@event.listens_for(Base.metadata, "after_create")
def _create_trigger_function(target, connection, **kwargs):
    connection.execute(text(_TRIGGER_FUNCTION_SQL))


@event.listens_for(Base.metadata, "after_create")
def _create_updated_at_triggers(target, connection, **kwargs):
    """
    Creates an updated_at trigger on every table that has an updated_at column.
    Iterates all tables in Base.metadata after they've been created.

    Only tables using TimestampMixin (which includes updated_at) get triggers.
    Tables using CreatedAtMixin (created_at only) are skipped automatically
    because they don't have an updated_at column.

    Tables that get the trigger:
      users, properties, payments, subscriptions,
      agent_availability, bookings,
      media_assets, kyc_documents,
      user_notification_settings, reports
    """
    for table in target.tables.values():
        if "updated_at" in table.columns:
            connection.execute(text(_TRIGGER_SQL.format(table_name=table.name)))
