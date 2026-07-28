# app/db/base_all.py

"""
db/base_all.py
──────────────────────────────────────────────────────────────────────────────
Imports every SQLAlchemy model so they register with Base.metadata.

This file exists for ONE reason: Alembic's env.py imports it to discover
all tables before running autogenerate. If a model is not imported here,
Alembic will not detect it and will generate a migration that drops the
table from your database.

    # alembic/env.py
    from app.db.base_all import Base        # triggers everything below
    target_metadata = Base.metadata

IMPORT ORDER — FK dependency tiers:
  A table's FK targets must be imported BEFORE the table itself.
  SQLAlchemy resolves string-based relationships lazily at mapper
  configuration time (after ALL models load), so the order here only
  matters for Python-level import-time FK resolution — not relationship().

  Tier 0 — no FK deps (or self-referencing only)
  Tier 1 — FK to Tier 0 only
  Tier 2 — FK to Tier 0 + Tier 1
  Tier 3 — FK to Tier 0 + Tier 1 + Tier 2
  Tier 4 — FK to Tier 3 (reviews gate on completed bookings)

ADDING A NEW MODEL:
  1. Create the model file in the appropriate domain
  2. Identify its FK tier (what tables does it depend on?)
  3. Add the import below in the correct tier section
  4. Run: alembic revision --autogenerate -m "add_<table_name>"
  5. Review the generated migration before applying

# noqa: F401 — all imports here are intentionally "unused" by Python's
# perspective. The side-effect of importing registers the model class
# with Base.metadata, which is the entire purpose of this file.
"""

# ── Optional sanity check ─────────────────────────────────────────────────────
# Uncomment during development to catch a missed model before Alembic runs.
#
import logging

from app.db.base import Base  # noqa: F401  ← re-exported for alembic/env.py
from app.domains.admin.models import (  # noqa: F401
    AdminAuditLog,  # FK: admin_id → users (polymorphic target)
)
from app.domains.analytics.models import (  # noqa: F401
    PropertyAnalytics,  # FK: property_id, user_id
)

# ── Tier 3 — FKs to Tier 2 tables ────────────────────────────────────────────
from app.domains.bookings.models import (  # noqa: F401  # noqa: F401
    AgentAvailability,  # FK: agent_id (users), property_id
    Booking,  # FK: property_id, user_id, payment_id (nullable)
)
from app.domains.consent.models import (  # noqa: F401
    UserConsentLog,  # FK: user_id → users
)
from app.domains.kyc.models import (  # noqa: F401  # noqa: F401
    KycAdminReview,  # FK: kyc_document_id, admin_id (users)
    KycDocument,  # FK: user_id → users
)
from app.domains.media.models import (  # noqa: F401  # noqa: F401
    MediaAsset,  # FK: uploaded_by → users
    PropertyImage,  # FK: property_id, media_asset_id
    VirtualTour,  # FK: property_id
)
from app.domains.messaging.models import (  # noqa: F401  # noqa: F401
    Conversation,  # FK: property_id (nullable)
    ConversationParticipant,  # FK: conversation_id, user_id
    Message,  # FK: conversation_id, sender_id (users)
)
from app.domains.notifications.models import (  # noqa: F401
    Notification,  # FK: user_id → users
    UserNotificationSettings,  # FK: user_id → users (UNIQUE)
)
from app.domains.payments.models import (  # noqa: F401
    Payment,  # FK: user_id → users
)

# ── Tier 1 — FKs to User only ────────────────────────────────────────────────
# These tables reference users.id and nothing else.
# Reports and AdminAuditLog use polymorphic target_id — no DB FK constraint.
# ── Tier 2 — FKs to User + Property (and Tier 1 tables) ──────────────────────
from app.domains.properties.models import (  # noqa: F401  # noqa: F401
    Property,  # FK: owner_id, rented_by_user_id, approved_by → users
    PropertyFeature,  # no FKs — imported here with its domain
    PropertyFeatureMap,  # FK: property_id, feature_id
)
from app.domains.reports.models import (  # noqa: F401
    Report,  # FK: reporter_id, reviewed_by → users (polymorphic target)
)

# ── Tier 4 — FKs to Booking (Tier 3) ─────────────────────────────────────────
from app.domains.reviews.models import (  # noqa: F401
    AgentReview,  # FK: agent_id, reviewer_id (users), booking_id
    Review,  # FK: user_id, property_id, booking_id
)
from app.domains.search.models import (  # noqa: F401  # noqa: F401
    Favorite,  # FK: user_id, property_id
    SavedSearch,  # FK: user_id → users
    SearchHistory,  # FK: user_id, property_id (nullable)
)
from app.domains.subscriptions.models import (  # noqa: F401
    Subscription,  # FK: user_id, payment_id
)

# ── Tier 0 — No FK dependencies ──────────────────────────────────────────────
# User has one self-referencing FK (suspended_by → users.id).
# SQLAlchemy handles self-refs without ordering constraints.
# PropertyFeature has no FKs at all — imported with its domain in Tier 1.
from app.domains.users.models import (  # noqa: F401
    User,
)

_log = logging.getLogger(__name__)
_n = len(Base.metadata.tables)
_log.debug("base_all: %d tables registered with Base.metadata", _n)
assert _n == 27, (
    f"Expected 27 tables in Base.metadata, got {_n}. "
    "A model was added or removed without updating base_all.py."
)


# ── Table registry (for human reference) ─────────────────────────────────────
# 27 tables total after this file loads:
#
# Tier 0 (1):  users
#
# Tier 1 (10): properties, property_features,
#              payments, notifications, user_notification_settings,
#              user_consent_log, media_assets, kyc_documents,
#              reports, admin_audit_log, saved_searches
#
# Tier 2 (9):  property_feature_map, property_images, virtual_tours,
#              property_analytics, agent_availability, conversations,
#              favorites, search_history, kyc_admin_reviews
#
# Tier 3 (4):  bookings, subscriptions,
#              conversation_participants, messages
#
# Tier 4 (2):  reviews, agent_reviews
