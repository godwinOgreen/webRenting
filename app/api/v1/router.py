"""
Central API router.

Aggregates all domain routers under /api/v1.
Each domain's router.py already defines its own prefix and tags —
this file just includes them. Add new domains here as they're built.
"""

from fastapi import APIRouter

from app.domains.auth.router import router as auth_router
from app.domains.users.router import router as users_router
from app.domains.users.router import agents_router
from app.domains.properties.router import router as properties_router
from app.domains.bookings.router import router as bookings_router
from app.domains.payments.router import router as payments_router
from app.domains.subscriptions.router import router as subscriptions_router
from app.domains.messaging.router import router as messaging_router
from app.domains.notifications.router import router as notifications_router

api_router = APIRouter()

api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(agents_router)
api_router.include_router(properties_router)
api_router.include_router(bookings_router)
api_router.include_router(payments_router)
api_router.include_router(subscriptions_router)
api_router.include_router(messaging_router)
api_router.include_router(notifications_router)

# ─── Add new domain routers here as they're built ───
# from app.domains.media.router import router as media_router
# api_router.include_router(media_router)
# from app.domains.reviews.router import router as reviews_router
# api_router.include_router(reviews_router)
# from app.domains.kyc.router import router as kyc_router
# api_router.include_router(kyc_router)
# from app.domains.search.router import router as search_router
# api_router.include_router(search_router)
# from app.domains.analytics.router import router as analytics_router
# api_router.include_router(analytics_router)
# from app.domains.reports.router import router as reports_router
# api_router.include_router(reports_router)
# from app.domains.consent.router import router as consent_router
# api_router.include_router(consent_router)
# from app.domains.admin.router import router as admin_router
# api_router.include_router(admin_router)