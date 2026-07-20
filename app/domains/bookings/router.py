"""
domains/bookings/router.py

HTTP layer for bookings and agent availability. No business logic.

Two role surfaces on the same resource:
  - Renter actions: create, cancel, list own bookings
  - Agent actions: create/delete slots, confirm/reject, list bookings
    on their properties

Booking creation requires require_verified_renter()
(Rule 3) — renters book, agents don't.
Availability slot management requires require_agent() since
only agents manage their own calendars.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.domains.bookings.schemas import (
    AvailabilitySlotCreate,
    AvailabilitySlotRead,
    BookingCreate,
    BookingRead,
    BookingRejectRequest,
)
from app.domains.bookings.service import BookingService
from app.domains.users.models import User
from app.permissions.guards import require_agent, require_verified_renter
from app.shared.schemas import PaginatedResponse, SuccessResponse

router = APIRouter(tags=["bookings"])


# ── Agent availability ────────────────────────────────────────────────────────

@router.post(
    "/availability",
    response_model=SuccessResponse[AvailabilitySlotRead],
    status_code=201,
    summary="Create a viewing availability slot (agent only)",
)
async def create_slot(
    data: AvailabilitySlotCreate,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[AvailabilitySlotRead]:
    svc = BookingService(db)
    return SuccessResponse.ok(
        data=await svc.create_slot(current_user, data),
        message="Availability slot created",
    )


@router.get(
    "/availability/mine",
    response_model=SuccessResponse[list[AvailabilitySlotRead]],
    summary="List own availability slots (agent only)",
)
async def list_my_slots(
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[list[AvailabilitySlotRead]]:
    svc = BookingService(db)
    return SuccessResponse.ok(data=await svc.list_my_slots(current_user))


@router.get(
    "/properties/{property_id}/availability",
    response_model=SuccessResponse[list[AvailabilitySlotRead]],
    summary="List open slots for a property (public)",
)
async def list_open_slots(
    property_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[list[AvailabilitySlotRead]]:
    """Public — renters browsing a property need to see bookable times."""
    svc = BookingService(db)
    return SuccessResponse.ok(data=await svc.list_open_slots(property_id))


@router.delete(
    "/availability/{slot_id}",
    response_model=SuccessResponse,
    summary="Delete an unbooked availability slot (agent only)",
)
async def delete_slot(
    slot_id: uuid.UUID,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    svc = BookingService(db)
    await svc.delete_slot(slot_id, current_user)
    return SuccessResponse.empty("Availability slot deleted")


# ── Bookings (renter) ─────────────────────────────────────────────────────────

@router.post(
    "/bookings",
    response_model=SuccessResponse[BookingRead],
    status_code=201,
    summary="Book a property viewing (renter, requires subscription + KYC)",
)
async def create_booking(
    data: BookingCreate,
    current_user: User = Depends(require_verified_renter()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[BookingRead]:
    """
    Rule 3: bookings require an active subscription AND verified KYC.
    Both checks are enforced together by require_verified_renter().
    """
    svc = BookingService(db)
    return SuccessResponse.ok(
        data=await svc.create_booking(current_user, data),
        message="Booking request sent",
    )


@router.get(
    "/bookings/mine",
    response_model=PaginatedResponse[BookingRead],
    summary="List own bookings (renter)",
)
async def list_my_bookings(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[BookingRead]:
    svc = BookingService(db)
    return await svc.list_for_renter(current_user, page, per_page)


@router.get(
    "/bookings/{booking_id}",
    response_model=SuccessResponse[BookingRead],
    summary="Get own booking details (renter)",
)
async def get_my_booking(
    booking_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[BookingRead]:
    svc = BookingService(db)
    return SuccessResponse.ok(data=await svc.get_for_renter(booking_id, current_user))


@router.post(
    "/bookings/{booking_id}/cancel",
    response_model=SuccessResponse[BookingRead],
    summary="Cancel own booking (renter)",
)
async def cancel_booking(
    booking_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[BookingRead]:
    svc = BookingService(db)
    return SuccessResponse.ok(
        data=await svc.cancel(booking_id, current_user),
        message="Booking cancelled",
    )


# ── Bookings (agent) ──────────────────────────────────────────────────────────

@router.get(
    "/bookings/agent/mine",
    response_model=PaginatedResponse[BookingRead],
    summary="List bookings on own properties (agent)",
)
async def list_agent_bookings(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[BookingRead]:
    svc = BookingService(db)
    return await svc.list_for_agent(current_user, page, per_page)


@router.get(
    "/bookings/agent/{booking_id}",
    response_model=SuccessResponse[BookingRead],
    summary="Get booking on own property (agent)",
)
async def get_agent_booking(
    booking_id: uuid.UUID,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[BookingRead]:
    svc = BookingService(db)
    return SuccessResponse.ok(
        data=await svc.get_for_agent(booking_id, current_user)
    )


@router.post(
    "/bookings/{booking_id}/confirm",
    response_model=SuccessResponse[BookingRead],
    summary="Confirm a pending booking (agent)",
)
async def confirm_booking(
    booking_id: uuid.UUID,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[BookingRead]:
    svc = BookingService(db)
    return SuccessResponse.ok(
        data=await svc.confirm(booking_id, current_user),
        message="Booking confirmed",
    )


@router.post(
    "/bookings/{booking_id}/reject",
    response_model=SuccessResponse[BookingRead],
    summary="Reject a pending booking (agent, reason required)",
)
async def reject_booking(
    booking_id: uuid.UUID,
    data: BookingRejectRequest,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[BookingRead]:
    svc = BookingService(db)
    return SuccessResponse.ok(
        data=await svc.reject(booking_id, current_user, data.rejection_reason),
        message="Booking rejected",
    )