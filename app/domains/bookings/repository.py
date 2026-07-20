"""
domains/bookings/repository.py

Data access for bookings and agent availability.

No FK between Booking and AgentAvailability (see models.py docstring)
— this repository is where the two are coordinated: find an open slot,
create the booking, flip is_booked, all within one flush.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.bookings.models import AgentAvailability, Booking, BookingStatus
from app.domains.bookings.schemas import AvailabilitySlotCreate, BookingCreate


class BookingRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Availability reads ───────────────────────────────────────────────────

    async def get_slot_by_id(
        self, slot_id: uuid.UUID
    ) -> Optional[AgentAvailability]:
        return await self.db.get(AgentAvailability, slot_id)

    async def list_open_slots(
        self, property_id: uuid.UUID
    ) -> list[AgentAvailability]:
        """Open, future slots for a property — what a renter can book."""
        result = await self.db.execute(
            select(AgentAvailability)
            .where(
                AgentAvailability.property_id == property_id,
                AgentAvailability.is_booked.is_(False),
                AgentAvailability.slot_start > datetime.now(tz=timezone.utc),
            )
            .order_by(AgentAvailability.slot_start)
        )
        return list(result.scalars().all())

    async def list_agent_slots(
        self, agent_id: uuid.UUID, property_id: Optional[uuid.UUID] = None
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
        await self.db.delete(slot)
        await self.db.flush()

    async def set_slot_booked(
        self, slot: AgentAvailability, booked: bool
    ) -> AgentAvailability:
        slot.is_booked = booked
        await self.db.flush()
        return slot

    # ── Booking reads ─────────────────────────────────────────────────────────

    async def get_by_id(self, booking_id: uuid.UUID) -> Optional[Booking]:
        return await self.db.get(Booking, booking_id)

    async def get_by_id_and_user(
        self, booking_id: uuid.UUID, user_id: uuid.UUID
    ) -> Optional[Booking]:
        result = await self.db.execute(
            select(Booking).where(
                Booking.id == booking_id, Booking.user_id == user_id
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_agent(
        self, booking_id: uuid.UUID, agent_id: uuid.UUID
    ) -> Optional[Booking]:
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
        base = select(Booking).where(Booking.user_id == user_id)
        total = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        result = await self.db.execute(
            base.order_by(Booking.visit_time.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        return list(result.scalars().all()), total

    async def list_for_agent(
        self, agent_id: uuid.UUID, page: int, per_page: int
    ) -> tuple[list[Booking], int]:
        """All bookings for properties owned by this agent."""
        from app.domains.properties.models import Property
        base = (
            select(Booking)
            .join(Property, Booking.property_id == Property.id)
            .where(Property.owner_id == agent_id)
        )
        total = (
            await self.db.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        result = await self.db.execute(
            base.order_by(Booking.visit_time.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        return list(result.scalars().all()), total

    async def count_pending_for_user(self, user_id: uuid.UUID) -> int:
        """Used to enforce MAX_PENDING_BOOKINGS_PER_USER."""
        result = await self.db.execute(
            select(func.count()).select_from(Booking).where(
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
        rejection_reason: Optional[str] = None,
    ) -> Booking:
        booking.status = status
        if rejection_reason is not None:
            booking.rejection_reason = rejection_reason
        await self.db.flush()
        return booking