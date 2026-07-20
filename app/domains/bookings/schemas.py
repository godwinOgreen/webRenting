"""
Request/response schemas for bookings and agent availability.

Bookings are created against an AgentAvailability slot, but there is
no FK between them (see bookings/models.py docstring) — the renter
sends a slot_id at creation time, and the service resolves it to the
matching availability row, verifies it's open, and copies its
visit_time onto the new Booking.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ── Agent Availability ────────────────────────────────────────────────────────

class AvailabilitySlotCreate(BaseModel):
    """Payload for an agent to open a new viewing time slot."""
    property_id: uuid.UUID
    slot_start: datetime
    slot_end: datetime

    @field_validator("slot_end")
    @classmethod
    def end_after_start(cls, v: datetime, info) -> datetime:
        start = info.data.get("slot_start")
        if start is not None and v <= start:
            raise ValueError("slot_end must be after slot_start")
        return v


class AvailabilitySlotRead(BaseModel):
    """Public view of an availability slot."""
    id: uuid.UUID
    property_id: uuid.UUID
    slot_start: datetime
    slot_end: datetime
    is_booked: bool

    model_config = ConfigDict(from_attributes=True)


# ── Booking ───────────────────────────────────────────────────────────────────

class BookingCreate(BaseModel):
    """
    Renter creates a booking by selecting an open availability slot.
    visit_time and property_id are derived from the slot by the service,
    not supplied directly.
    """
    slot_id: uuid.UUID
    booking_type: str = Field(..., description="physical_inspection|virtual")
    notes: Optional[str] = Field(None, max_length=2000)

    @field_validator("booking_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        allowed = {"physical_inspection", "virtual"}
        if v not in allowed:
            raise ValueError(f"booking_type must be one of: {allowed}")
        return v


class BookingRejectRequest(BaseModel):
    """Agent rejecting a pending booking — reason is required (Rule 7)."""
    rejection_reason: str = Field(..., min_length=1, max_length=500)


class BookingRead(BaseModel):
    """Full booking view for renter or agent."""
    id: uuid.UUID
    property_id: uuid.UUID
    user_id: uuid.UUID
    visit_time: datetime
    booking_type: str
    status: str
    rejection_reason: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BookingCard(BaseModel):
    """Lightweight view for booking list screens."""
    id: uuid.UUID
    property_id: uuid.UUID
    property_title: str
    visit_time: datetime
    booking_type: str
    status: str

    model_config = ConfigDict(from_attributes=True)