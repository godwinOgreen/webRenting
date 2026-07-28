"""
domains/bookings/repository.py

Data access for bookings and agent availability.

No FK between Booking and AgentAvailability (see models.py docstring)
— this repository is where the two are coordinated: find an open slot,
create the booking, flip is_booked, all within one flush.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.bookings.models import AgentAvailability, Booking, BookingStatus
from app.domains.bookings.schemas import AvailabilitySlotCreate, BookingCreate


class BookingRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Availability reads ───────────────────────────────────────────────────

    async def get_slot_by_id(self, slot_id: uuid.UUID) -> AgentAvailability | None:
        """Fetch a single availability slot by ID."""
        return await self.db.get(AgentAvailability, slot_id)

    async def get_slot_by_id_for_update(self, slot_id: uuid.UUID) -> AgentAvailability | None:
        """
        Fetch a slot with an exclusive row-level lock (SELECT FOR UPDATE).
        Used during booking creation to prevent double-booking race conditions.
        """
        result = await self.db.execute(
            select(AgentAvailability).where(AgentAvailability.id == slot_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def list_open_slots(self, property_id: uuid.UUID) -> list[AgentAvailability]:
        """Open, future slots for a property — what a renter can book."""
        result = await self.db.execute(
            select(AgentAvailability)
            .where(
                AgentAvailability.property_id == property_id,
                AgentAvailability.is_booked.is_(False),
                AgentAvailability.slot_start > func.now(),
            )
            .order_by(AgentAvailability.slot_start)
        )
        return list(result.scalars().all())

    async def list_agent_slots(
        self, agent_id: uuid.UUID, property_id: uuid.UUID | None = None
    ) -> list[AgentAvailability]:
        """All slots an agent created, for their calendar management view."""
        q = select(AgentAvailability).where(AgentAvailability.agent_id == agent_id)
        if property_id:
            q = q.where(AgentAvailability.property_id == property_id)
        result = await self.db.execute(q.order_by(AgentAvailability.slot_start))
        return list(result.scalars().all())

    # ── Availability writes ───────────────────────────────────────────────────

    async def create_slot(
        self, agent_id: uuid.UUID, data: AvailabilitySlotCreate
    ) -> AgentAvailability:
        """Create a new availability slot."""
        slot = AgentAvailability(
            agent_id=agent_id,
            property_id=data.property_id,
            slot_start=data.slot_start,
            slot_end=data.slot_end,
        )
        self.db.add(slot)
        await self.db.flush()
        return slot

    async def delete_slot(self, slot: AgentAvailability) -> None:
        """Delete an availability slot."""
        await self.db.delete(slot)
        await self.db.flush()

    async def set_slot_booked(self, slot: AgentAvailability, booked: bool) -> AgentAvailability:
        """Set slot as booked or available."""
        slot.is_booked = booked
        await self.db.flush()
        return slot

    # ── Booking reads ─────────────────────────────────────────────────────────

    async def get_by_id(self, booking_id: uuid.UUID) -> Booking | None:
        """Fetch a single booking by ID."""
        return await self.db.get(Booking, booking_id)

    async def get_by_id_and_user(self, booking_id: uuid.UUID, user_id: uuid.UUID) -> Booking | None:
        """Fetch a booking only if it belongs to the given user."""
        result = await self.db.execute(
            select(Booking).where(Booking.id == booking_id, Booking.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_agent(
        self, booking_id: uuid.UUID, agent_id: uuid.UUID
    ) -> Booking | None:
        """
        Fetch a booking only if the requesting agent owns the property
        it's for. Joins through Property to check ownership.
        """
        from app.domains.properties.models import Property

        result = await self.db.execute(
            select(Booking)
            .join(Property, Booking.property_id == Property.id)
            .where(Booking.id == booking_id, Property.owner_id == agent_id)
        )
        return result.scalar_one_or_none()

    async def list_for_renter(
        self, user_id: uuid.UUID, page: int, per_page: int
    ) -> tuple[list[Booking], int]:
        """Paginated bookings for a renter."""
        base = select(Booking).where(Booking.user_id == user_id)

        count_q = select(func.count()).select_from(base.subquery())
        data_q = (
            base.order_by(Booking.visit_time.desc()).offset((page - 1) * per_page).limit(per_page)
        )

        count_res = await self.db.execute(count_q)
        data_res = await self.db.execute(data_q)

        return list(data_res.scalars().all()), count_res.scalar_one()

    async def list_for_agent(
        self, agent_id: uuid.UUID, page: int, per_page: int
    ) -> tuple[list[Booking], int]:
        """Paginated bookings for all properties owned by an agent."""
        from app.domains.properties.models import Property

        base = (
            select(Booking)
            .join(Property, Booking.property_id == Property.id)
            .where(Property.owner_id == agent_id)
        )

        count_q = select(func.count()).select_from(base.subquery())
        data_q = (
            base.order_by(Booking.visit_time.desc()).offset((page - 1) * per_page).limit(per_page)
        )

        count_res = await self.db.execute(count_q)
        data_res = await self.db.execute(data_q)

        return list(data_res.scalars().all()), count_res.scalar_one()

    async def count_pending_for_user(self, user_id: uuid.UUID) -> int:
        """Count pending bookings for rate limiting."""
        result = await self.db.execute(
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.user_id == user_id,
                Booking.status == BookingStatus.PENDING,
            )
        )
        return result.scalar_one()

    # ── Booking writes ────────────────────────────────────────────────────────

    async def create(
        self,
        user_id: uuid.UUID,
        property_id: uuid.UUID,
        visit_time: datetime,
        data: BookingCreate,
    ) -> Booking:
        """Create a new booking in PENDING state."""
        booking = Booking(
            property_id=property_id,
            user_id=user_id,
            visit_time=visit_time,
            booking_type=data.booking_type,
            notes=data.notes,
        )
        self.db.add(booking)
        await self.db.flush()
        return booking

    async def update_status(
        self,
        booking: Booking,
        status: BookingStatus,
        *,
        rejection_reason: str | None = None,
    ) -> Booking:
        """Update booking status and optional rejection reason."""
        booking.status = status
        if rejection_reason is not None:
            booking.rejection_reason = rejection_reason
        await self.db.flush()
        return booking
