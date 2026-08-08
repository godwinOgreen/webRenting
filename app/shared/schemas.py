# app/shared/schemas.py

"""
Shared Pydantic schemas and Python-only enums.

These are NOT PostgreSQL enum types. They are Pydantic-compatible
definitions used for API request validation and shared response formats.

Domain-specific schemas live in their own domain:
  app/domains/payments/schemas.py     -> PaymentInitializeRequest
  app/domains/users/schemas.py        -> UserCreate, UserResponse
  app/shared/schemas.py               -> shared across domains

What lives here:
  1. Python-only enums -- validated at API boundary, stored as strings in DB
  2. Common request schemas -- pagination, filters
  3. Common response schemas -- standard envelope, paginated list, cursor

Why SubscriptionPlanType is here (not in subscriptions domain):
  It's used by three domains:
    - payments/schemas.py       (PaymentInitializeRequest.plan_type)
    - subscriptions/schemas.py  (SubscriptionCreate.plan_type)
    - auth/schemas.py           (RegistrationRequest.plan_type)
  Defining it once in shared/ avoids circular imports between domains.

Response envelope:
  Every endpoint returns {"success": true/false, ...} so the frontend
  has ONE response shape to parse for both success and error cases.
  The global exception handler in main.py produces the error shape;
  SuccessResponse/PaginatedResponse produce the success shape.
"""

from __future__ import annotations

import enum
import math
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

# ─── Python-only Enums ───────────────────────────────────────────────────────


class SubscriptionPlanType(str, enum.Enum):
    """
    Available subscription plans. Python-only -- NOT a PostgreSQL enum.

    Database stores plain strings ("renter" or "agent").
    Validated at API boundary by Pydantic.
    Adding a new plan type requires zero migrations.

    Used by:
      payments/schemas.py       -> PaymentInitializeRequest.plan_type
      subscriptions/schemas.py  -> SubscriptionCreate.plan_type
      auth/schemas.py           -> RegistrationRequest.plan_type

    Pricing: see RENTER_PLAN_PRICE_KOBO and AGENT_PLAN_PRICE_KOBO
    in app/constants.py.
    """

    RENTER = "renter"
    AGENT = "agent"


# ─── Common Request Schemas ──────────────────────────────────────────────────


class PaginationParams(BaseModel):
    """
    Reusable query-parameter model for paginated endpoints.
    Inject via Depends() in routers that return PaginatedResponse.

    Usage:
        @router.get("/properties")
        async def list_properties(params: PaginationParams = Depends()):
            items, total = await repo.list(db, params.page, params.per_page)
            return PaginatedResponse.paginate(items, total, params.page, params.per_page)
    """

    page: int = Field(
        default=1,
        ge=1,
        description="Page number (1-indexed).",
    )
    per_page: int = Field(
        default=DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"Items per page. Max {MAX_PAGE_SIZE}.",
    )

    @property
    def offset(self) -> int:
        """Calculated SQL OFFSET value."""
        return (self.page - 1) * self.per_page

    @property
    def limit(self) -> int:
        """Calculated SQL LIMIT value."""
        return self.per_page


class DateRangeParams(BaseModel):
    """
    Optional date range filter. Used by analytics, reports, bookings.

    Both fields are optional -- omit both to get all time.
    Omit start_date for "up to end_date".
    Omit end_date for "from start_date onwards".
    """

    start_date: str | None = Field(
        default=None,
        description="ISO 8601 start date (inclusive). e.g. '2026-01-01'.",
    )
    end_date: str | None = Field(
        default=None,
        description="ISO 8601 end date (inclusive). e.g. '2026-01-31'.",
    )


# ─── Common Response Schemas ─────────────────────────────────────────────────

T = TypeVar("T")


class SuccessResponse(BaseModel, Generic[T]):
    """
    Standard wrapper for single-resource success responses.

    success is always True here -- False lives on the error response
    shape returned by main.py's global exception handler.

    Usage:
        return SuccessResponse.ok(data=user, message="User registered")
        return SuccessResponse.ok(data=property)
        return SuccessResponse.empty(message="Notification marked as read")
        return SuccessResponse.created(data=new_property)
    """

    success: bool = True
    message: str = "Success"
    data: T | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @classmethod
    def ok(cls, data: T, message: str = "Success") -> SuccessResponse[T]:
        """Standard success with data payload."""
        return cls(data=data, message=message)

    @classmethod
    def empty(cls, message: str = "Success") -> SuccessResponse[None]:
        """For endpoints that confirm an action without returning data."""
        return cls(data=None, message=message)

    @classmethod
    def created(cls, data: T, message: str = "Created successfully") -> SuccessResponse[T]:
        """For POST endpoints that create a resource (router sets status_code=201)."""
        return cls(data=data, message=message)


class PaginationMeta(BaseModel):
    """Page metadata embedded inside PaginatedResponse."""

    page: int
    per_page: int
    total: int
    total_pages: int


class PaginatedResponse(BaseModel, Generic[T]):
    """
    Standard wrapper for paginated list responses.

    Mirrors SuccessResponse envelope so the frontend can parse
    all responses with the same code.

    Usage:
        items, total = await repo.list(db, page, per_page)
        return PaginatedResponse.paginate(items, total, page, per_page)
    """

    success: bool = True
    message: str = "Success"
    items: list[T]
    total: int
    data: list[T]
    meta: PaginationMeta

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @classmethod
    def paginate(
        cls,
        items: list[T],
        total: int,
        page: int,
        per_page: int,
        message: str = "Success",
    ) -> PaginatedResponse[T]:
        """
        Build a PaginatedResponse from raw query results.

        total_pages uses ceiling division. If total=0, total_pages=0.
        """
        item_list = list(items)
        total_pages = math.ceil(total / per_page) if total > 0 else 0
        return cls(
            items=item_list,
            total=total,
            data=item_list,
            message=message,
            meta=PaginationMeta(
                page=page,
                per_page=per_page,
                total=total,
                total_pages=total_pages,
            ),
        )


class CursorPage(BaseModel, Generic[T]):
    """
    Cursor-based pagination for infinite-scroll feeds.
    Used by messaging (conversation list, message thread) and
    notifications inbox -- any feed where the user scrolls rather
    than clicks "page 2".

    next_cursor: an opaque string the client sends as ?cursor=<next_cursor>
    on the next request. None means this is the last page.
    has_more: convenience flag so the client doesn't have to check
    if next_cursor is None.

    Cursor encoding is implementation-specific per endpoint -- typically
    a base64-encoded timestamp or row ID. Encoding/decoding lives in
    the service layer, not here.
    """

    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @classmethod
    def of(cls, items: list[T], next_cursor: str | None) -> CursorPage[T]:
        """Build a CursorPage. has_more is derived from next_cursor."""
        return cls(
            items=items,
            next_cursor=next_cursor,
            has_more=next_cursor is not None,
        )
