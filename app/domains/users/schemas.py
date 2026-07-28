"""
domains/users/schemas.py

Request/response schemas for the users domain.

Three views of a user, with different field exposure:
  UserRead         -- full self-view (GET /users/me). Includes email,
                      kyc_status, suspension state, agent profile fields.
  UserPublicRead   -- public view of any user (GET /users/{id}). Hides
                      email, suspension details, admin fields. Shows only
                      what a stranger should see.
  AgentPublicRead  -- public agent profile card. Used on listing pages
                      and agent directory -- adds agency/bio/experience.

The KYC-overwritten fields (first_name, last_name, dob) are in UserRead
because the authenticated user should see their own verified data. They
are NOT in UserPublicRead -- DoB is personal data that is never public.
"""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

# ── Self-view (authenticated user reading own profile) ────────────────────────


class UserRead(BaseModel):
    """Full self-view of the authenticated user's profile."""

    id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    dob: date | None = None
    phone: str | None = None
    profile_image_url: str | None = None
    role: str
    kyc_status: str
    verified: bool
    # Agent-only fields (None for renters)
    agency_name: str | None = None
    agent_bio: str | None = None
    license_number: str | None = None
    years_experience: int | None = None
    # State
    is_suspended: bool

    model_config = ConfigDict(from_attributes=True)


# ── Public view (anyone viewing another user) ─────────────────────────────────


class UserPublicRead(BaseModel):
    """
    Safe public view. Email, DoB, KYC status, and suspension details
    are hidden -- personal data not suitable for public exposure.
    """

    id: uuid.UUID
    first_name: str
    last_name: str
    profile_image_url: str | None = None
    role: str
    verified: bool

    model_config = ConfigDict(from_attributes=True)


class AgentPublicRead(UserPublicRead):
    """
    Extended public profile for agents. Shown on listing detail pages
    and the agent directory. Adds professional profile fields.
    """

    agency_name: str | None = None
    agent_bio: str | None = None
    years_experience: int | None = None


# ── Update ────────────────────────────────────────────────────────────────────


class UserUpdate(BaseModel):
    """
    Fields a user can update on their own profile.

    first_name/last_name/dob are excluded -- they are overwritten by
    the KYC provider on approval and should not be manually editable
    after verification. Before KYC, the user can set them; after KYC,
    the values come from the identity document.

    Allowing post-KYC edits would silently diverge the profile from the
    verified identity -- which defeats the point of KYC entirely.
    """

    phone: str | None = Field(None, max_length=20)
    profile_image_url: str | None = Field(None, max_length=512)
    # Agent-only fields -- silently ignored for renters in the service
    agency_name: str | None = Field(None, max_length=255)
    agent_bio: str | None = None
    license_number: str | None = Field(None, max_length=100)
    years_experience: int | None = Field(None, ge=0, le=60)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ── Notification settings ─────────────────────────────────────────────────────


class NotificationSettingsRead(BaseModel):
    """Current notification preferences for the authenticated user."""

    email_enabled: bool
    push_enabled: bool
    sms_enabled: bool
    notify_new_message: bool
    notify_booking_update: bool
    notify_listing_approved: bool
    notify_saved_search: bool
    notify_listing_expiry: bool
    notify_subscription: bool
    marketing_email: bool
    marketing_sms: bool

    model_config = ConfigDict(from_attributes=True)


class NotificationSettingsUpdate(BaseModel):
    """
    Partial update -- only supplied fields are changed.
    All fields are Optional so the client can send a single-field PATCH.
    """

    email_enabled: bool | None = None
    push_enabled: bool | None = None
    sms_enabled: bool | None = None
    notify_new_message: bool | None = None
    notify_booking_update: bool | None = None
    notify_listing_approved: bool | None = None
    notify_saved_search: bool | None = None
    notify_listing_expiry: bool | None = None
    notify_subscription: bool | None = None
    marketing_email: bool | None = None
    marketing_sms: bool | None = None

    model_config = ConfigDict(extra="forbid")
