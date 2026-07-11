"""
domains/auth/schemas.py

Request and response schemas for authentication endpoints.
No domain model imports -- auth has no tables of its own.

Design decisions:
  - Refresh token travels in an HTTP-only cookie, not the response body.
    TokenResponse only contains the access token. Cookie set/cleared by
    the router, not the service.
  - Role on registration limited to renter | agent. Admin/moderator
    accounts are created by existing admins via admin endpoints.
  - Email normalised to lowercase in the validator before storage.
  - Password max is 72 bytes (bcrypt hard limit). Rejected at schema
    layer with a clear message so security.hash_password() never sees
    oversized input.
"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.constants import PASSWORD_MIN_LENGTH, PASSWORD_MAX_BYTES


# ── Registration ──────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(
        ...,
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_BYTES,
        description=(
            f"{PASSWORD_MIN_LENGTH}–{PASSWORD_MAX_BYTES} characters. "
            "At least one number or symbol."
        ),
    )
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name:  str = Field(..., min_length=1, max_length=100)
    role: Literal["renter", "agent"] = "renter"
    # NDPR: explicit terms acceptance required at registration.
    # Client must send True -- False is rejected, ensuring no accidental
    # empty POST slips through without consent.
    accept_terms: bool = Field(
        ...,
        description="Must be True. Recorded in user_consent_log.",
    )

    @field_validator("email")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        """Store emails lowercase -- DB unique index is on LOWER(email)."""
        return v.lower().strip()

    @field_validator("accept_terms")
    @classmethod
    def must_accept_terms(cls, v: bool) -> bool:
        if not v:
            raise ValueError("You must accept the terms of service to register")
        return v

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        has_digit   = any(c.isdigit()   for c in v)
        has_special = any(not c.isalnum() for c in v)
        if not (has_digit or has_special):
            raise ValueError(
                "Password must contain at least one number or special character"
            )
        return v

    @field_validator("first_name", "last_name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        return v.strip()


# ── Login ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

    @field_validator("email")
    @classmethod
    def normalise_email(cls, v: str) -> str:
        return v.lower().strip()


# ── Token / response ──────────────────────────────────────────────────────────

class AuthUserRead(BaseModel):
    """
    Minimal user snapshot returned alongside tokens. Keeps the login
    response self-contained -- frontend can populate the user store
    without a separate GET /users/me call.
    """
    id: uuid.UUID
    email: str
    first_name: str
    last_name: str
    role: str
    kyc_status: str
    is_suspended: bool

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    """
    Returned by POST /auth/register and POST /auth/login.

    access_token: short-lived JWT sent in Authorization header.
    refresh_token is NOT here -- set as HTTP-only cookie by the router.
    Keeping it out of the body prevents JavaScript from reading it (XSS).
    """
    access_token: str
    token_type: str = "bearer"
    user: AuthUserRead


class RefreshResponse(BaseModel):
    """
    Returned by POST /auth/refresh. User payload omitted -- hasn't
    changed. New refresh token is set as a cookie by the router.
    """
    access_token: str
    token_type: str = "bearer"