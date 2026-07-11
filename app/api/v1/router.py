# app/api/v1/router.py

"""
Central API router.

Aggregates all domain routers under /api/v1.
Each domain's router.py exports a `router` object.

UNCOMMENT each import as that domain's router is built.
"""

from fastapi import APIRouter

# ─── Phase 4+: Uncomment as each domain router is built ───

# from app.domains.auth.router import router as auth_router
# from app.domains.users.router import router as users_router
# from app.domains.properties.router import router as properties_router
# from app.domains.bookings.router import router as bookings_router
# from app.domains.payments.router import router as payments_router
# from app.domains.subscriptions.router import router as subscriptions_router
# from app.domains.messaging.router import router as messaging_router
# from app.domains.notifications.router import router as notifications_router
# from app.domains.media.router import router as media_router
# from app.domains.reviews.router import router as reviews_router
# from app.domains.kyc.router import router as kyc_router
# from app.domains.search.router import router as search_router
# from app.domains.analytics.router import router as analytics_router
# from app.domains.reports.router import router as reports_router
# from app.domains.consent.router import router as consent_router
# from app.domains.admin.router import router as admin_router


api_router = APIRouter()

# ─── Phase 4+: Uncomment as each domain router is built ───

# api_router.include_router(auth_router,          prefix="/auth",          tags=["Authentication"])
# api_router.include_router(users_router,         prefix="/users",         tags=["Users"])
# api_router.include_router(properties_router,    prefix="/properties",    tags=["Properties"])
# api_router.include_router(bookings_router,      prefix="/bookings",      tags=["Bookings"])
# api_router.include_router(payments_router,      prefix="/payments",      tags=["Payments"])
# api_router.include_router(subscriptions_router, prefix="/subscriptions", tags=["Subscriptions"])
# api_router.include_router(messaging_router,     prefix="/messages",      tags=["Messaging"])
# api_router.include_router(notifications_router, prefix="/notifications", tags=["Notifications"])
# api_router.include_router(media_router,         prefix="/media",         tags=["Media"])
# api_router.include_router(reviews_router,       prefix="/reviews",       tags=["Reviews"])
# api_router.include_router(kyc_router,           prefix="/kyc",           tags=["KYC"])
# api_router.include_router(search_router,        prefix="/search",        tags=["Search"])
# api_router.include_router(analytics_router,     prefix="/analytics",     tags=["Analytics"])
# api_router.include_router(reports_router,       prefix="/reports",       tags=["Reports"])
# api_router.include_router(consent_router,       prefix="/consent",       tags=["Consent"])
# api_router.include_router(admin_router,         prefix="/admin",         tags=["Admin"])
