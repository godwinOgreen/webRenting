"""
core/security.py

Pure cryptography and token logic — no FastAPI imports, no HTTP
exceptions, no database access. This module only knows how to:
  - hash and verify passwords
  - create and decode JWTs

core/dependencies.py (next file) is responsible for translating
failures here into the appropriate HTTP responses (401, etc.) via
core/exceptions.py. Keeping this module framework-agnostic makes it
trivially unit-testable and reusable outside a request context (e.g.
a CLI script, or a Celery task that needs to mint a service token).

──────────────────────────────────────────────────────────────────────
DEVIATION FROM Standard 21 — flagged explicitly
──────────────────────────────────────────────────────────────────────
Standard 21 specifies "bcrypt (passlib)". This module uses the `bcrypt`
library directly instead of passlib's CryptContext wrapper.

Verified: passlib (last released 2020) is broken against modern bcrypt
(5.x). It probes `bcrypt.__about__.__version__`, an attribute removed
from current bcrypt releases, and fails on password verification with:

    ValueError: password cannot be longer than 72 bytes, truncate
    manually if necessary

bcrypt itself — the hashing algorithm Standard 21 actually cares about
— is used correctly below. passlib was only ever a convenience wrapper
around it; dropping the wrapper does not violate the intent of the
standard. If this is unacceptable, pin bcrypt <4.1 underneath passlib,
but that means carrying a known-stale crypto dependency indefinitely.
──────────────────────────────────────────────────────────────────────

Token types (Standard 18 — Authentication Standards):
  Access Token:   JWT, 30 minutes, sent in Authorization header
  Refresh Token:  JWT, 7 days, sent in a secure HTTP-only cookie

Both token types carry a "type" claim ("access" / "refresh") so a
refresh token can never be accepted where an access token is expected,
and vice versa — even though both are signed with the same SECRET_KEY.

Tokens carry ONLY identity (sub, type, jti) — no roles, no permissions.
Authorization (role checks, admin level, suspension status) is resolved
from the database on every gated request via core/permissions/guards.py.
This prevents stale authorization data from granting access that should
have been revoked mid-session (admin demotes user, subscription expires,
account gets suspended).

The "jti" claim is a unique token identifier. Not checked against a
blocklist today, but establishes the claim now so a future token-
revocation feature (e.g. "log out everywhere") doesn't require a
breaking change to every previously-issued token's shape.

──────────────────────────────────────────────────────────────────────
PARKED DOMAIN LOGIC (build later — not in this module)
──────────────────────────────────────────────────────────────────────
OAuth verification (Google, Facebook)  → app/domains/auth/oauth.py
Paystack webhook HMAC verification     → app/domains/payments/webhooks.py
Role / permission guards               → app/core/permissions/guards.py
──────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import settings

# ─── Password Hashing ────────────────────────────────────────────────────────
#
# bcrypt has a hard 72-byte input limit (not 72 characters — 72 bytes,
# which matters for multi-byte UTF-8 passwords). Silently truncating
# would mean two different passwords sharing a 72-byte prefix hashing
# identically — a real security weakening. We reject oversized input
# explicitly instead of truncating.


_BCRYPT_MAX_BYTES = 72
_BCRYPT_ROUNDS = 12  # cost factor — current industry-standard default


def hash_password(plain_password: str) -> str:
    """
    Hash a plaintext password with bcrypt.

    Returns a UTF-8 string safe to store in users.password_hash.
    bcrypt's own salt is embedded in the output — no separate salt
    column is needed on the users table.

    Raises:
        ValueError: password encodes to more than 72 bytes. Reject
        this at the schema validation layer (schemas/auth) with a
        clear message before it ever reaches this function.
    """
    pw_bytes = plain_password.encode("utf-8")
    if len(pw_bytes) > _BCRYPT_MAX_BYTES:
        raise ValueError(
            f"Password too long ({len(pw_bytes)} bytes) — bcrypt supports "
            f"a maximum of {_BCRYPT_MAX_BYTES} bytes. Reject this at the "
            f"schema validation layer (schemas/auth) with a clear message "
            f"before it ever reaches this function."
        )
    hashed = bcrypt.hashpw(pw_bytes, bcrypt.gensalt(rounds=_BCRYPT_ROUNDS))
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Check a plaintext password against a stored bcrypt hash.

    Returns False (never raises) on any mismatch or malformed hash —
    callers should not need to distinguish "wrong password" from "bad
    stored value"; both mean authentication fails the same way.
    """
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8"),
        )
    except (ValueError, TypeError):
        # Malformed hash (e.g. corrupted DB value) or oversized input —
        # treat as no match. Never raise from this function.
        return False


# ─── Token Types ─────────────────────────────────────────────────────────────


class TokenType(enum.StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


# ─── Token Exceptions ────────────────────────────────────────────────────────
#
# core/dependencies.py catches these and translates them into
# UnauthorizedException (see core/exceptions.py) for HTTP responses.
# Defined here rather than imported from core/exceptions.py so this
# module has zero dependencies beyond config — it can be unit tested,
# or used by a script, without pulling in the FastAPI exception stack.


class TokenError(Exception):
    """Base class for all token-related failures raised by this module."""


class TokenExpiredError(TokenError):
    """The token's exp claim has passed."""


class InvalidTokenError(TokenError):
    """Bad signature, malformed token, or wrong 'type' claim."""


# ─── JWT Creation ─────────────────────────────────────────────────────────────


def _create_token(
    subject: str | uuid.UUID,
    token_type: TokenType,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Internal helper — both create_access_token and create_refresh_token
    delegate here. Not exported; callers use the two public functions
    below so the (token_type, expiry) pairing can never drift from the
    Standard 18 contract (30 min access / 7 day refresh).
    """
    now = datetime.now(tz=UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type.value,
        "iat": now,
        "exp": now + expires_delta,
        # Unique token ID. Not checked against anything today, but
        # establishes the claim now so a future token-revocation /
        # blacklist feature (e.g. "log out everywhere") doesn't require
        # a breaking change to every previously-issued token's shape.
        "jti": str(uuid.uuid4()),
    }
    if extra_claims:
        payload.update(extra_claims)

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def create_access_token(
    subject: str | uuid.UUID,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """
    Create a short-lived access token (Standard 18: 30 minutes default,
    configurable via ACCESS_TOKEN_EXPIRE_MINUTES).

    subject is typically the user's UUID as a string.

    extra_claims can carry non-sensitive context the API wants on every
    request without a DB lookup. Do NOT put anything authorization-
    critical here (e.g. role, kyc_status, subscription state) — a
    30-minute-old claim can go stale mid-session and silently grant
    access that should have been revoked. permissions/guards.py
    re-checks these from the database on every gated request; this
    token only proves identity.
    """
    return _create_token(
        subject,
        TokenType.ACCESS,
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        extra_claims,
    )


def create_refresh_token(subject: str | uuid.UUID) -> str:
    """
    Create a long-lived refresh token (Standard 18: 7 days default,
    configurable via REFRESH_TOKEN_EXPIRE_DAYS).

    No extra_claims parameter, by design. A refresh token's only job is
    proving identity well enough to mint a new access token — it should
    carry the absolute minimum payload, reducing what a leaked
    long-lived token could expose.
    """
    return _create_token(
        subject,
        TokenType.REFRESH,
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


# ─── JWT Decoding ─────────────────────────────────────────────────────────────


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    """
    Decode and validate a JWT, enforcing both signature/expiry AND that
    the token's "type" claim matches expected_type.

    This second check is what stops a refresh token from being usable
    as an access token, and vice versa — see module docstring.

    Raises:
        TokenExpiredError: the token's exp claim has passed.
        InvalidTokenError: bad signature, malformed token, or the
                            'type' claim does not match expected_type.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as e:
        # NOTE: must be caught before the broader InvalidTokenError
        # below — PyJWT's ExpiredSignatureError is itself a subclass
        # of InvalidTokenError, so except-order matters here.
        raise TokenExpiredError("Token has expired") from e
    except jwt.InvalidTokenError as e:
        raise InvalidTokenError("Token is invalid") from e

    if payload.get("type") != expected_type.value:
        raise InvalidTokenError(
            f"Expected a {expected_type.value!r} token, got {payload.get('type')!r}"
        )

    return payload


def get_subject_from_token(token: str, expected_type: TokenType) -> str:
    """
    Convenience wrapper: decode_token() + extract just the subject
    (the user ID string). Most callers — get_current_user in
    core/dependencies.py — only need this, not the full claim set.
    """
    payload = decode_token(token, expected_type)
    subject = payload.get("sub")
    if subject is None:
        raise InvalidTokenError("Token is missing the 'sub' claim")
    return subject
