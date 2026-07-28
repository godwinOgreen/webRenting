"""
core/exceptions.py

The application's exception hierarchy (05_LOGGING_AND_ERRORS.md, §11).
Every exception raised anywhere in app/domains/*, app/tasks/*, or
app/integrations/* that should produce a specific HTTP response is one
of these — never a bare Exception, never an HTTPException raised
directly from a service or repository (HTTPException is a FastAPI/
Starlette concept; raising it outside main.py's exception handler
couples business logic to the web framework).

    raise NotFoundException(message="Property not found")     # ✅
    raise Exception("Property not found")                     # ❌ never

A single global handler registered in main.py catches BaseAppException
and converts it to the standard error response shape (03_API_AND_
RESPONSES.md, §17):

    @app.exception_handler(BaseAppException)
    async def app_exception_handler(request, exc: BaseAppException):
        logger.warning(exc.message, extra=exc.log_context)
        return JSONResponse(status_code=exc.status_code, content=exc.to_dict())

That handler is the ONLY place these exceptions are caught and logged.
Domain code just raises and walks away — no try/except needed at the
router or service layer for the common cases below.

──────────────────────────────────────────────────────────────────────
Hierarchy
──────────────────────────────────────────────────────────────────────
    BaseAppException                (500 — should rarely be raised directly)
    ├── ValidationException         (400 — request data invalid)
    ├── UnauthorizedException       (401 — missing/invalid/expired auth)
    ├── ForbiddenException          (403 — authenticated but not allowed)
    ├── NotFoundException           (404 — resource does not exist)
    ├── ConflictException           (409 — state conflict, e.g. double-booking)
    ├── RateLimitException          (429 — too many requests)
    ├── PaymentException            (402 — payment failed/required)
    └── KYCException                (403 — KYC required or not verified)

KYCException and ForbiddenException are both 403. They are kept
separate because the client needs to render different UI for each:
"you're not allowed to do this" (ForbiddenException) vs. "complete
identity verification to continue" (KYCException, which the frontend
can route straight to the KYC upload screen). Standard 19's
permissions/guards.py is the intended caller of both:

    require_kyc()          → raises KYCException
    require_subscription() → raises ForbiddenException(
                                  error_code="subscription_required")
    require_role(role)     → raises ForbiddenException(
                                  error_code="insufficient_role")

core/security.py's TokenExpiredError / InvalidTokenError are NOT
subclasses of anything here — that module is deliberately framework-
agnostic (see its docstring). core/dependencies.py is responsible for
catching those two and re-raising as UnauthorizedException.
"""

from __future__ import annotations

from typing import Any


class BaseAppException(Exception):
    """
    Base class for every application-raised exception.

    Subclasses set status_code and default_message as CLASS attributes.
    Instances may override the message per-raise, attach field-level
    errors (for ValidationException), an error_code for machine-readable
    client handling, and log_context for structured logging in the
    global exception handler.

    errors and log_context default to None in __init__, never to a
    mutable {} at the class level — a shared mutable default would mean
    every exception instance secretly shares (and mutates) the same dict.
    """

    status_code: int = 500
    default_message: str = "An unexpected error occurred"
    default_error_code: str = "internal_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        errors: dict[str, list[str]] | None = None,
        error_code: str | None = None,
        log_context: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.errors = errors or {}
        self.error_code = error_code or self.default_error_code
        # Extra structured fields for the global handler to pass into
        # logger.warning(..., extra=exc.log_context) — e.g.
        # {"property_id": ..., "user_id": ...}. Never includes anything
        # from the NEVER LOG list (passwords, tokens, etc. — Standard 10).
        self.log_context = log_context or {}
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        """
        Standard error response body (03_API_AND_RESPONSES.md, §17):
            {"success": false, "message": "...", "errors": {...}}

        errors is included even when empty — keeps the response shape
        consistent for frontend clients that always destructure it.
        """
        return {
            "success": False,
            "message": self.message,
            "errors": self.errors,
            "error_code": self.error_code,
        }

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"status_code={self.status_code}, "
            f"message={self.message!r}, "
            f"error_code={self.error_code!r})"
        )


# ── 400 ─────────────────────────────────────────────────────────────────────


class ValidationException(BaseAppException):
    """
    Request data failed validation beyond what Pydantic schema-level
    validation already catches — typically a business-rule check that
    needs DB or cross-field context Pydantic alone can't express
    (e.g. "max_price must be >= min_price" when both are independently
    valid numbers).

    errors should map field name → list of error messages, mirroring
    Pydantic's own validation error shape so the frontend can render
    both kinds of failures identically:

        raise ValidationException(
            message="Validation failed",
            errors={"price": ["Must be greater than 0"]},
        )
    """

    status_code = 400
    default_message = "Validation failed"
    default_error_code = "validation_error"


# ── 401 ─────────────────────────────────────────────────────────────────────


class UnauthorizedException(BaseAppException):
    """
    Missing, invalid, or expired authentication. The client should
    treat this as "log in again" — for an expired access token, the
    frontend's first move should be attempting a silent refresh via
    the refresh token cookie before forcing a full re-login.

    core/dependencies.py raises this when:
      - No Authorization header present on a protected route
      - core.security.decode_token() raises TokenExpiredError
      - core.security.decode_token() raises InvalidTokenError
    """

    status_code = 401
    default_message = "Authentication required"
    default_error_code = "unauthorized"


# ── 402 ─────────────────────────────────────────────────────────────────────


class PaymentException(BaseAppException):
    """
    A payment could not be processed, or a paid action was attempted
    without the required payment having succeeded.

    Used by payment_service.py for Paystack failures, and potentially
    by subscription_service.py if a renewal payment is declined.
    Distinct from ForbiddenException("subscription required") — that
    case means "you've never paid"; PaymentException means "a payment
    attempt just failed."
    """

    status_code = 402
    default_message = "Payment failed"
    default_error_code = "payment_failed"


# ── 403 ─────────────────────────────────────────────────────────────────────


class ForbiddenException(BaseAppException):
    """
    The user is authenticated but not allowed to perform this action.

    Common error_code values used by permissions/guards.py — not an
    exhaustive enum, just the conventional values other layers should
    expect when branching on this field client-side:
      "insufficient_role"       — wrong role for this endpoint
      "subscription_required"   — no active subscription (Rule 3)
      "not_resource_owner"      — e.g. editing another agent's listing
    """

    status_code = 403
    default_message = "You do not have permission to perform this action"
    default_error_code = "forbidden"


class KYCException(ForbiddenException):
    """
    KYC verification is required and missing, pending, or rejected.

    Subclasses ForbiddenException (same 403 status) rather than
    BaseAppException directly — a KYC failure IS a forbidden-action
    case, just one specific and common enough in this platform
    (Rule 3: subscription + KYC gate) to warrant its own type so
    permissions/guards.py.require_kyc() and frontend routing logic
    can target it precisely:

        except KYCException:
            redirect_to_kyc_upload_screen()
        except ForbiddenException:
            show_generic_403_page()

    Because KYCException IS-A ForbiddenException, a handler written
    for the parent still catches KYC failures too — callers that don't
    care about the distinction lose nothing by only handling the base.
    """

    default_message = "KYC verification is required to perform this action"
    default_error_code = "kyc_required"


# ── 404 ─────────────────────────────────────────────────────────────────────


class NotFoundException(BaseAppException):
    """
    The requested resource does not exist — or, per Standard 20 (no
    physical deletes), exists but is in a state that makes it
    effectively invisible to this caller (e.g. a suspended user's
    profile requested by a non-admin, or an archived property fetched
    by ID outside the owning agent's session). Repositories should
    raise this rather than returning None and letting a service guess
    at the reason.
    """

    status_code = 404
    default_message = "Resource not found"
    default_error_code = "not_found"


# ── 409 ─────────────────────────────────────────────────────────────────────


class ConflictException(BaseAppException):
    """
    The request conflicts with the current state of the resource.

    Primary use cases in this platform:
      - Double-booking: AgentAvailability slot already is_booked=True
      - Duplicate review: UNIQUE (user_id, booking_id) constraint hit
      - Re-submitting a property that's already pending_review
      - Paystack webhook replay on an already-processed paystack_reference
    """

    status_code = 409
    default_message = "This action conflicts with the current state"
    default_error_code = "conflict"


# ── 429 ─────────────────────────────────────────────────────────────────────


class RateLimitException(BaseAppException):
    """
    Too many requests (Standard 21 rate limits: login 5/min,
    register 5/min, search 60/min, default 100/min). Raised by
    rate-limiting middleware, not typically by domain services.
    """

    status_code = 429
    default_message = "Too many requests — please try again shortly"
    default_error_code = "rate_limited"
