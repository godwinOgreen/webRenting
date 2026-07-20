"""
domains/bookings/service.py

Business logic for bookings and agent availability.

No-double-booking rule (Decision 6 / Rule 6):
  Booking and AgentAvailability have no FK between them. This service
  is where the two are coordinated:
    1. Look up the requested slot by slot_id
    2. Verify it belongs to the target property and is_booked=False
       and is in the future
    3. Create the Booking, copying visit_time/property_id from the slot
    4. Flip the slot's is_booked=True in the SAME transaction

  Both writes happen via the same AsyncSession before commit — if
  anything fails between steps 3 and 4, the whole transaction rolls
  back (get_db() handles this), so a slot can never end up locked
  without a matching booking, or vice versa.

Rejection requires a reason (Rule 7): enforced by BookingRejectRequest
schema requiring a non-empty rejection_reason — see bookings/schemas.py.

State machine (bookings/models.py BookingStatus docstring):
  PENDING   → CONFIRMED  (agent confirms)
  PENDING   → REJECTED   (agent rejects, reason required) → slot freed
  PENDING   → CANCELLED  (renter cancels)                  → slot freed
  CONFIRMED → COMPLETED  (Celery, after visit_time passes)
  CONFIRMED → CANCELLED  (renter cancels a confirmed booking) → slot freed
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import MAX_PENDING_BOOKINGS_PER_USER
from app.core.exceptions import ConflictException, ForbiddenException, NotFoundException
from app.domains.bookings.models import AgentAvailability, Booking, BookingStatus
from app.domains.bookings.repository import BookingRepository
from app.domains.bookings.schemas import (
    AvailabilitySlotCreate,
    AvailabilitySlotRead,
    BookingCreate,
    BookingRead,
)
from app.domains.properties.models import Property
from app.domains.users.models import User
from app.shared.schemas import PaginatedResponse

logger = logging.getLogger(__name__)

# States from which a slot can be freed back to available
_FREES_SLOT = {BookingStatus.REJECTED, BookingStatus.CANCELLED}


class BookingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = BookingRepository(db)

    async def _get_property_for_owner(
        self, property_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Optional[Property]:
        """
        Direct model query (not a PropertyRepository import) — services
        may query other domains' models directly but must not import
        another domain's repository or service class
        (01_ARCHITECTURE.md section 3).
        """
        result = await self.db.execute(
            select(Property).where(
                Property.id == property_id, Property.owner_id == owner_id
            )
        )
        return result.scalar_one_or_none()

    # ── Agent availability ────────────────────────────────────────────────────

    async def create_slot(
        self, agent: User, data: AvailabilitySlotCreate
    ) -> AvailabilitySlotRead:
        """
        Agent opens a new viewing slot for one of their properties.
        Verifies the agent actually owns the property.
        """
        prop = await self._get_property_for_owner(
            data.property_id, agent.id
        )
        if prop is None:
            raise NotFoundException(
                message="Property not found or not owned by you",
            )
        slot = await self.repo.create_slot(agent.id, data)
        logger.info(
            "Availability slot created",
            extra={"slot_id": str(slot.id), "agent_id": str(agent.id)},
        )
        return AvailabilitySlotRead.model_validate(slot)

    async def list_open_slots(
        self, property_id: uuid.UUID
    ) -> list[AvailabilitySlotRead]:
        """Public — renters browsing a property see open slots."""
        slots = await self.repo.list_open_slots(property_id)
        return [AvailabilitySlotRead.model_validate(s) for s in slots]

    async def list_my_slots(self, agent: User) -> list[AvailabilitySlotRead]:
        slots = await self.repo.list_agent_slots(agent.id)
        return [AvailabilitySlotRead.model_validate(s) for s in slots]

    async def delete_slot(self, slot_id: uuid.UUID, agent: User) -> None:
        """
        Agent removes an unbooked slot. Booked slots cannot be deleted
        directly — the agent must reject/cancel the booking first.
        """
        slot = await self.repo.get_slot_by_id(slot_id)
        if slot is None or slot.agent_id != agent.id:
            raise NotFoundException(message="Availability slot not found")
        if slot.is_booked:
            raise ConflictException(
                message="Cannot delete a booked slot. Reject or cancel the "
                        "booking first.",
                error_code="slot_is_booked",
            )
        await self.repo.delete_slot(slot)

    # ── Create booking ────────────────────────────────────────────────────────

    async def create_booking(
        self,
        renter: User,
        data: BookingCreate,
    ) -> BookingRead:
        """
        Create a booking against an open slot. See module docstring for
        the slot-coordination sequence.

        Raises:
            NotFoundException: slot doesn't exist
            ConflictException: slot already booked, slot is in the past,
                                or renter has too many pending bookings
        """
        pending_count = await self.repo.count_pending_for_user(renter.id)
        if pending_count >= MAX_PENDING_BOOKINGS_PER_USER:
            raise ConflictException(
                message=(
                    f"You have reached the maximum of "
                    f"{MAX_PENDING_BOOKINGS_PER_USER} pending bookings. "
                    "Please wait for a response or cancel an existing request."
                ),
                error_code="max_pending_bookings",
            )

        slot = await self.repo.get_slot_by_id(data.slot_id)
        if slot is None:
            raise NotFoundException(message="Availability slot not found")

        if not slot.is_available:
            raise ConflictException(
                message="This slot is no longer available",
                error_code="slot_unavailable",
                log_context={"slot_id": str(slot.id)},
            )

        booking = await self.repo.create(
            user_id=renter.id,
            property_id=slot.property_id,
            visit_time=slot.slot_start,
            data=data,
        )

        # Lock the slot in the same transaction
        await self.repo.set_slot_booked(slot, True)

        logger.info(
            "Booking created",
            extra={
                "booking_id": str(booking.id),
                "user_id": str(renter.id),
                "property_id": str(slot.property_id),
            },
        )
        return BookingRead.model_validate(booking)

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_for_renter(
        self, booking_id: uuid.UUID, renter: User
    ) -> BookingRead:
        booking = await self.repo.get_by_id_and_user(booking_id, renter.id)
        if booking is None:
            raise NotFoundException(message="Booking not found")
        return BookingRead.model_validate(booking)

    async def get_for_agent(
        self, booking_id: uuid.UUID, agent: User
    ) -> BookingRead:
        booking = await self.repo.get_by_id_for_agent(booking_id, agent.id)
        if booking is None:
            raise NotFoundException(message="Booking not found")
        return BookingRead.model_validate(booking)

    async def list_for_renter(
        self, renter: User, page: int, per_page: int
    ) -> PaginatedResponse[BookingRead]:
        bookings, total = await self.repo.list_for_renter(renter.id, page, per_page)
        items = [BookingRead.model_validate(b) for b in bookings]
        return PaginatedResponse.paginate(items, total, page, per_page)

    async def list_for_agent(
        self, agent: User, page: int, per_page: int
    ) -> PaginatedResponse[BookingRead]:
        bookings, total = await self.repo.list_for_agent(agent.id, page, per_page)
        items = [BookingRead.model_validate(b) for b in bookings]
        return PaginatedResponse.paginate(items, total, page, per_page)

    # ── State transitions ─────────────────────────────────────────────────────

    async def confirm(self, booking_id: uuid.UUID, agent: User) -> BookingRead:
        """PENDING → CONFIRMED. Agent only, must own the property."""
        booking = await self.repo.get_by_id_for_agent(booking_id, agent.id)
        if booking is None:
            raise NotFoundException(message="Booking not found")
        if booking.status != BookingStatus.PENDING:
            raise ConflictException(
                message="Only pending bookings can be confirmed",
                error_code="invalid_state_transition",
            )
        updated = await self.repo.update_status(booking, BookingStatus.CONFIRMED)
        logger.info("Booking confirmed", extra={"booking_id": str(booking_id)})
        return BookingRead.model_validate(updated)

    async def reject(
        self, booking_id: uuid.UUID, agent: User, reason: str
    ) -> BookingRead:
        """PENDING → REJECTED. Agent only. Frees the slot."""
        booking = await self.repo.get_by_id_for_agent(booking_id, agent.id)
        if booking is None:
            raise NotFoundException(message="Booking not found")
        if booking.status != BookingStatus.PENDING:
            raise ConflictException(
                message="Only pending bookings can be rejected",
                error_code="invalid_state_transition",
            )
        updated = await self.repo.update_status(
            booking, BookingStatus.REJECTED, rejection_reason=reason
        )
        await self._free_slot_for_booking(updated)
        logger.info("Booking rejected", extra={"booking_id": str(booking_id)})
        return BookingRead.model_validate(updated)

    async def cancel(self, booking_id: uuid.UUID, renter: User) -> BookingRead:
        """PENDING | CONFIRMED → CANCELLED. Renter only. Frees the slot."""
        booking = await self.repo.get_by_id_and_user(booking_id, renter.id)
        if booking is None:
            raise NotFoundException(message="Booking not found")
        if booking.status not in (BookingStatus.PENDING, BookingStatus.CONFIRMED):
            raise ConflictException(
                message="This booking cannot be cancelled",
                error_code="invalid_state_transition",
            )
        updated = await self.repo.update_status(booking, BookingStatus.CANCELLED)
        await self._free_slot_for_booking(updated)
        logger.info("Booking cancelled", extra={"booking_id": str(booking_id)})
        return BookingRead.model_validate(updated)

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _free_slot_for_booking(self, booking: Booking) -> None:
        """
        Find the AgentAvailability slot matching this booking's
        property_id + visit_time and set is_booked=False.

        No FK between booking and slot — this is a best-effort match
        on (property_id, slot_start == visit_time). If no matching slot
        is found, this is a no-op.
        """
        result = await self.db.execute(
            select(AgentAvailability).where(
                AgentAvailability.property_id == booking.property_id,
                AgentAvailability.slot_start == booking.visit_time,
                AgentAvailability.is_booked.is_(True),
            )
        )
        slot = result.scalar_one_or_none()
        if slot is not None:
            await self.repo.set_slot_booked(slot, False)