# app/constants.py

"""
Centralized constants — no magic numbers in code.

Every constant here is referenced by at least one model, service, or task.
When a value needs to change, change it here — one place, everywhere updates.

Organization:
  Section order matches the domain order in base_all.py.
  Each constant has a comment explaining what it controls and where it's used.
"""


# ─── Subscriptions ───────────────────────────────────────────────────────────

SUBSCRIPTION_DURATION_DAYS = 30
# Used by: Subscription.create_from_payment()
# The default length of a subscription period.

SUBSCRIPTION_WARNING_DAYS = 3
# Used by: Subscription.is_expiring_soon, Celery subscription_check task
# How many days before expiry to send a renewal reminder notification.

SUBSCRIPTION_EXPIRY_CHECK_HOUR_UTC = 1
# Used by: Celery subscription_check task (beat schedule)
# Which hour (UTC) the Celery task runs to expire subscriptions.

RENTER_PLAN_PRICE_NGN = 1_000
# Used by: display purposes, admin dashboard
# Renter subscription price in naira.

AGENT_PLAN_PRICE_NGN = 10_000
# Used by: display purposes, admin dashboard
# Agent subscription price in naira.

RENTER_PLAN_PRICE_KOBO = 100_000
# Used by: Subscription.renewal_amount_kobo, payment_service
# Renter subscription price in kobo (₦1,000 = 100,000 kobo).

AGENT_PLAN_PRICE_KOBO = 1_000_000
# Used by: Subscription.renewal_amount_kobo, payment_service
# Agent subscription price in kobo (₦10,000 = 1,000,000 kobo).
# FIX: was 10_000_000 (10x overcharge). ₦10,000 × 100 = 1,000,000.

MAX_SUBSCRIPTION_EXTENSIONS = 3
# Used by: admin_service (limits how many times admin can extend a subscription)
# Prevents abuse of grant_subscription admin action.


# ─── Properties ──────────────────────────────────────────────────────────────

LISTING_EXPIRY_DAYS = 30
# Used by: property_service (sets expires_at when publishing)
# Celery listing_expiry task (archives when expires_at passes)
# How long a published listing stays live before expiring.

LISTING_WARNING_DAYS_BEFORE_EXPIRY = 7
# Used by: Celery listing_expiry task
# How many days before expiry to send the "listing expiring" notification.


# ─── Bookings ────────────────────────────────────────────────────────────────

MIN_SLOT_DURATION_MINUTES = 30
# Used by: booking_service (validates slot duration when agent creates slots)
# Minimum length of an AgentAvailability slot.

MAX_ADVANCE_BOOKING_DAYS = 30
# Used by: booking_service (validates how far ahead a booking can be made)
# Maximum days in the future a renter can book.

MAX_PENDING_BOOKINGS_PER_USER = 5
# Used by: booking_service (rejects if user already has 5 pending)
# Prevents a renter from blocking every agent on the platform.

BOOKING_AUTOCOMPLETE_HOURS = 2
# Used by: Celery auto_complete_bookings task
# Hours after visit_time before a CONFIRMED booking is auto-marked COMPLETED.


# ─── Reviews ─────────────────────────────────────────────────────────────────

MIN_RATING = 1
# Used by: Review + AgentReview CHECK constraints (f-string)
# Minimum star rating.

MAX_RATING = 5
# Used by: Review + AgentReview CHECK constraints (f-string)
# Maximum star rating.

REVIEW_WINDOW_DAYS = 30
# Used by: review_service (rejects review if more than 30 days after completion)
# How many days after a booking is completed a renter can leave a review.


# ─── Messaging ───────────────────────────────────────────────────────────────

MAX_MESSAGE_LENGTH = 5000
# Used by: Message CHECK constraint (f-string), messaging_service validation
# Maximum characters in a single message body.

MAX_CONVERSATIONS_PER_USER = 100
# Used by: messaging_service (soft limit, warns user)
# Prevents conversation spam without blocking legitimate use.


# ─── Media ───────────────────────────────────────────────────────────────────

MAX_PROPERTY_IMAGES = 20
# Used by: media_service (rejects upload if property already has 20 images)
# Maximum images per property listing.

MAX_VIRTUAL_TOURS = 5
# Used by: media_service (rejects if property already has 5 tours)
# Maximum virtual tours per property listing.

MAX_IMAGES_PER_UPLOAD = 10
# Used by: media_service (rejects batch upload exceeding this)
# Maximum images uploaded in a single request.

MAX_FILE_SIZE_KB = 10_000
# Used by: integrations/storage upload handler
# Maximum upload file size (10 MB).

ALLOWED_IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp"]
# Used by: integrations/storage upload handler
# MIME types allowed for image uploads.

ALLOWED_VIDEO_TYPES = ["video/mp4"]
# Used by: integrations/storage upload handler
# MIME types allowed for video uploads.


# ─── KYC ─────────────────────────────────────────────────────────────────────

ALLOWED_DOCUMENT_TYPES = ["national_id", "passport", "drivers_license"]
# Used by: kyc_service (validates document_type on submission)
# Must match KycDocumentType enum values.

KYC_WEBHOOK_TIMEOUT_HOURS = 24
# Used by: Celery kyc_timeout_check task
# How long the platform waits for a KYC provider webhook before
# marking the document as timed-out and notifying the user to retry.

VALID_KYC_PROVIDERS = {"smile_identity", "youverify"}
# Used by: kyc_service (validates provider name)
# Supported KYC verification providers.


# ─── Passwords ───────────────────────────────────────────────────────────────

PASSWORD_MIN_LENGTH = 8
# Used by: schemas/auth.py (schema-level validation, rejects before hashing)
# Minimum password length.

PASSWORD_MAX_BYTES = 72
# Used by: schemas/auth.py (schema-level validation)
# bcrypt hard limit — must match core/security.py _BCRYPT_MAX_BYTES.
# Rejected at schema level so user sees a clear message, not a server error.


# ─── Search ──────────────────────────────────────────────────────────────────

MAX_SEARCH_RADIUS_KM = 50
# Used by: search_service (caps the radius parameter)
# Maximum search radius in kilometers.

DEFAULT_SEARCH_PAGE_SIZE = 20
# Used by: search_service default pagination
# Default number of results per page.

MAX_SEARCH_PAGE_SIZE = 100
# Used by: search_service (caps page_size parameter)
# Maximum results per page (prevents abuse).

NEARBY_PLACES_RADIUS_METERS = 1_000
# Used by: integrations/google_places (nearby search on property detail page)
# Radius for gyms, hospitals, schools lookup.


# ─── Payments ────────────────────────────────────────────────────────────────

PAYMENT_SESSION_EXPIRY_MINUTES = 30
# Used by: payment_service (marks abandoned payment sessions as failed)
# How long a Paystack payment session is valid before we consider it abandoned.

PAYMENT_CURRENCY = "NGN"
# Used by: payment_service (sent to Paystack API), Subscription.renewal_amount_kobo
# The only currency the platform currently supports.

SUPPORTED_CURRENCIES = {"NGN"}
# Used by: property_service, payment_service (validates currency field)
# All currencies the platform supports. Add more when expanding.


# ─── Notifications ───────────────────────────────────────────────────────────

SAVED_SEARCH_ALERT_INTERVAL_HOURS = 6
# Used by: Celery saved_search_alerts task (beat schedule)
# How often to check for new matches on saved searches.

MAX_UNREAD_NOTIFICATIONS = 50
# Used by: notification_service (limits unread response payload)
# Maximum number of unread notifications returned on the bell/icon endpoint.

DAILY_ANALYTICS_HOUR_UTC = 0
# Used by: Celery daily_analytics task (beat schedule)
# Which hour (UTC) the daily analytics digest runs.

DAILY_ANALYTICS_MINUTE_UTC = 30
# Used by: Celery daily_analytics task (beat schedule)
# Which minute past the hour the daily analytics digest runs.

VALID_NOTIFICATION_RELATED_TYPES = {
    "booking", "property", "message", "subscription", "kyc", "report",
}
# Used by: notification_service (validates related_type on creation)
# Deep-link target types for notifications.


# ─── Analytics ───────────────────────────────────────────────────────────────

VALID_ANALYTICS_EVENT_TYPES = {
    "view", "contact", "booking", "favorite",
    "phone_reveal", "share", "search_impression",
}
# Used by: analytics_service (validates event_type on tracking)
# All tracked property interaction event types.

ANALYTICS_RETENTION_DAYS = 90
# Used by: Celery analytics_cleanup task
# How many days of raw analytics events are retained before aggregation/pruning.


# ─── Reports ─────────────────────────────────────────────────────────────────

REPORT_REASONS = [
    "fake_listing",
    "scam",
    "inappropriate_content",
    "harassment",
    "wrong_information",
    "other",
]
# Used by: report_service (validates reason on report creation)
# Must match ReportReason enum values.

MAX_REPORTS_PER_ENTITY = 3
# Used by: report_service anti-spam check
# Maximum times a user can report the same entity.

REPORT_COOLDOWN_HOURS = 24
# Used by: report_service anti-spam check
# Hours between reports on the same entity by the same user.


# ─── Consent ─────────────────────────────────────────────────────────────────

VALID_CONSENT_TYPES = {
    "terms_of_service",
    "privacy_policy",
    "data_processing",
    "marketing_email",
    "marketing_sms",
    "cookie_policy",
}
# Used by: consent_service (validates consent_type on creation)
# All consent types tracked for NDPR compliance.


# ─── Admin ───────────────────────────────────────────────────────────────────

VALID_ADMIN_ACTIONS = {
    "approve_property",
    "reject_property",
    "suspend_property",
    "feature_property",
    "verify_property",
    "suspend_user",
    "unsuspend_user",
    "change_role",
    "verify_agent",
    "override_kyc",
    "resolve_report",
    "dismiss_report",
    "grant_subscription",
    "approve_media",
    "reject_media",
}
# Used by: admin_service (validates action on audit log creation)
# All admin actions that create an AdminAuditLog entry.

VALID_AUDIT_TARGET_TYPES = {
    "user", "property", "report", "kyc_document", "subscription", "media_asset",
}
# Used by: admin_service (validates target_type on audit log creation)
# Entity types that admin actions can target.

MAX_AUDIT_LOG_DETAIL_LENGTH = 2000
# Used by: admin_service (truncates excessively long details)
# Prevents audit log rows from becoming unwieldy.


# ─── Pagination (general) ────────────────────────────────────────────────────

DEFAULT_PAGE_SIZE = 20
# Used by: PaginationParams schema, all paginated endpoints
# Default number of items per page.

MAX_PAGE_SIZE = 100
# Used by: PaginationParams schema, all paginated endpoints
# Maximum items per page (prevents abuse).