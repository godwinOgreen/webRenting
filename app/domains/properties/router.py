"""
domains/properties/router.py

HTTP layer for the properties domain. No business logic.

Lifecycle transitions are separate POST endpoints, not PATCH with a
status field — this makes the state machine explicit in the API
surface and lets us apply different guards per transition
(e.g. approve/reject are admin-only and live in the admin router).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db, get_optional_user
from app.domains.properties.schemas import (
    FeatureRead,
    PropertyCard,
    PropertyCreate,
    PropertyPublicRead,
    PropertyRead,
    PropertySearch,
    PropertyUpdate,
)
from app.domains.properties.service import PropertyService
from app.domains.users.models import User
from app.permissions.guards import require_agent
from app.shared.schemas import PaginatedResponse, SuccessResponse

router = APIRouter(prefix="/properties", tags=["properties"])


# ── GET /properties/features ─────────────────────────────────────────────────


@router.get(
    "/features",
    response_model=SuccessResponse[list[FeatureRead]],
    summary="List all available property features/amenities",
)
async def list_features(
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[list[FeatureRead]]:
    """Public endpoint — no auth required."""
    svc = PropertyService(db)
    return SuccessResponse.ok(data=await svc.list_features())


# ── GET /properties/mine ──────────────────────────────────────────────────────


@router.get(
    "/mine",
    response_model=PaginatedResponse[PropertyCard],
    summary="Agent's own listings",
)
async def list_mine(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    approval_status: str | None = Query(None),
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[PropertyCard]:
    """Retrieve all properties owned by the authenticated agent."""
    svc = PropertyService(db)
    return await svc.list_for_owner(current_user, page, per_page, approval_status)


# ── GET /properties/mine/{property_id} ────────────────────────────────────────


@router.get(
    "/mine/{property_id}",
    response_model=SuccessResponse[PropertyRead],
    summary="Get own listing (full details, all states)",
)
async def get_my_property(
    property_id: uuid.UUID,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[PropertyRead]:
    """View own listing in any state. Includes rejection_reason for rejected listings."""
    svc = PropertyService(db)
    return SuccessResponse.ok(data=await svc.get_for_owner(property_id, current_user))


# ── POST /properties ──────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=SuccessResponse[PropertyRead],
    status_code=201,
    summary="Create a new property listing (agent only)",
)
async def create_property(
    data: PropertyCreate,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[PropertyRead]:
    """Register a new property listing draft inside the system."""
    svc = PropertyService(db)
    return SuccessResponse.ok(
        data=await svc.create(current_user, data),
        message="Property created",
    )


# ── GET /properties (public search) ──────────────────────────────────────────


@router.get(
    "",
    response_model=PaginatedResponse[PropertyCard],
    summary="Search published property listings",
)
async def search_properties(
    city: str | None = Query(None),
    state: str | None = Query(None),
    property_type: str | None = Query(None),
    listing_status: str | None = Query(None),
    min_price: Decimal | None = Query(None, gt=0),
    max_price: Decimal | None = Query(None, gt=0),
    min_bedrooms: int | None = Query(None, ge=0),
    max_bedrooms: int | None = Query(None, ge=0),
    min_bathrooms: int | None = Query(None, ge=0),
    featured_only: bool = Query(False),
    verified_only: bool = Query(False),
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    radius_km: float | None = Query(None, gt=0, le=50),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[PropertyCard]:
    """Public search endpoint. Native Decimal query types are parsed out cleanly here."""
    filters = PropertySearch(
        city=city,
        state=state,
        property_type=property_type,
        listing_status=listing_status,
        min_price=min_price,
        max_price=max_price,
        min_bedrooms=min_bedrooms,
        max_bedrooms=max_bedrooms,
        min_bathrooms=min_bathrooms,
        featured_only=featured_only,
        verified_only=verified_only,
        lat=lat,
        lng=lng,
        radius_km=radius_km,
        page=page,
        per_page=per_page,
    )
    svc = PropertyService(db)
    return await svc.search(filters)


# ── GET /properties/{id} ──────────────────────────────────────────────────────


@router.get(
    "/{property_id}",
    response_model=SuccessResponse[PropertyPublicRead],
    summary="Get a single property listing",
)
async def get_property(
    property_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_optional_user),
) -> SuccessResponse[PropertyPublicRead]:
    """
    Public endpoint. Address hidden/shown based on address_hidden flag
    and whether the requesting user has a confirmed booking.
    """
    svc = PropertyService(db)
    # TODO: pass has_confirmed_booking=True when bookings domain is wired
    prop = await svc.get_public(property_id, requesting_user=current_user)
    return SuccessResponse.ok(data=prop)


# ── PATCH /properties/{id} ────────────────────────────────────────────────────


@router.patch(
    "/{property_id}",
    response_model=SuccessResponse[PropertyRead],
    summary="Update a listing (draft/rejected states only)",
)
async def update_property(
    property_id: uuid.UUID,
    data: PropertyUpdate,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[PropertyRead]:
    """Modify details of an editable listing."""
    svc = PropertyService(db)
    return SuccessResponse.ok(
        data=await svc.update(property_id, current_user, data),
        message="Property updated",
    )


# ── Lifecycle transitions ─────────────────────────────────────────────────────


@router.post(
    "/{property_id}/submit",
    response_model=SuccessResponse[PropertyRead],
    summary="Submit listing for admin review",
)
async def submit(
    property_id: uuid.UUID,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[PropertyRead]:
    """Trigger state validation: transition listing state to review queues."""
    svc = PropertyService(db)
    return SuccessResponse.ok(
        data=await svc.submit_for_review(property_id, current_user),
        message="Listing submitted for review",
    )


@router.post(
    "/{property_id}/publish",
    response_model=SuccessResponse[PropertyRead],
    summary="Publish an approved listing",
)
async def publish(
    property_id: uuid.UUID,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[PropertyRead]:
    svc = PropertyService(db)
    return SuccessResponse.ok(
        data=await svc.publish(property_id, current_user),
        message="Listing published",
    )


@router.post(
    "/{property_id}/reserve",
    response_model=SuccessResponse[PropertyRead],
    summary="Mark listing as reserved",
)
async def reserve(
    property_id: uuid.UUID,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[PropertyRead]:
    svc = PropertyService(db)
    return SuccessResponse.ok(
        data=await svc.mark_reserved(property_id, current_user),
        message="Listing marked as reserved",
    )


@router.post(
    "/{property_id}/mark-rented",
    response_model=SuccessResponse[PropertyRead],
    summary="Mark listing as rented",
)
async def mark_rented(
    property_id: uuid.UUID,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[PropertyRead]:
    svc = PropertyService(db)
    return SuccessResponse.ok(
        data=await svc.mark_rented(property_id, current_user),
        message="Listing marked as rented",
    )


@router.post(
    "/{property_id}/mark-sold",
    response_model=SuccessResponse[PropertyRead],
    summary="Mark listing as sold",
)
async def mark_sold(
    property_id: uuid.UUID,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[PropertyRead]:
    svc = PropertyService(db)
    return SuccessResponse.ok(
        data=await svc.mark_sold(property_id, current_user),
        message="Listing marked as sold",
    )


@router.post(
    "/{property_id}/archive",
    response_model=SuccessResponse[PropertyRead],
    summary="Archive a listing",
)
async def archive(
    property_id: uuid.UUID,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[PropertyRead]:
    svc = PropertyService(db)
    return SuccessResponse.ok(
        data=await svc.archive(property_id, current_user),
        message="Listing archived",
    )
