"""
domains/properties/service.py

Business logic for the properties domain — enforcing the 9-state
lifecycle (ApprovalStatus state machine), visibility rules, and
address-hiding logic.

State machine transitions (enforced here, not in the repository):
  DRAFT → PENDING_REVIEW     submit_for_review()
  PENDING_REVIEW → APPROVED  approve()         [admin only]
  PENDING_REVIEW → REJECTED  reject()          [admin only]
  REJECTED → PENDING_REVIEW  submit_for_review() (resubmit)
  APPROVED → PUBLISHED       publish()
  APPROVED → DRAFT           revert_to_draft()
  PUBLISHED → RESERVED       mark_reserved()
  PUBLISHED → RENTED         mark_rented()
  PUBLISHED → SOLD           mark_sold()
  PUBLISHED → ARCHIVED       archive() OR Celery expiry
  PUBLISHED → PENDING_REVIEW submit_for_review() (edit after publish)
  RESERVED → RENTED          mark_rented()
  RENTED → ARCHIVED          auto via Celery
  SOLD → ARCHIVED            auto via Celery
  ARCHIVED → PENDING_REVIEW  submit_for_review() (republish)
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import LISTING_EXPIRY_DAYS
from app.core.exceptions import ConflictException, NotFoundException
from app.domains.properties.models import ApprovalStatus, Property
from app.domains.properties.repository import PropertyRepository
from app.domains.properties.schemas import (
    FeatureRead,
    PropertyCard,
    PropertyCreate,
    PropertyImageRead,
    PropertyPublicRead,
    PropertyRead,
    PropertySearch,
    PropertyUpdate,
)
from app.domains.users.models import User, UserRole
from app.shared.schemas import PaginatedResponse

logger = logging.getLogger(__name__)

# States where an agent can still edit the listing content
_EDITABLE_STATES = {ApprovalStatus.DRAFT, ApprovalStatus.REJECTED}

# States where the listing is considered "active" for the agent
_ACTIVE_STATES = {
    ApprovalStatus.PUBLISHED,
    ApprovalStatus.RESERVED,
    ApprovalStatus.RENTED,
}


class PropertyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = PropertyRepository(db)

    # ── Create ────────────────────────────────────────────────────────────────

    async def create(
        self,
        owner: User,
        data: PropertyCreate,
    ) -> PropertyRead:
        """
        Create a new listing in DRAFT state.
        Only agents can create listings — enforced by require_agent()
        in the router before this method is called.

        Re-fetch via self.repo.get_by_id(prop.id) after repo.create()
        ensures features and images relationships are eagerly loaded
        before serialization, avoiding MissingGreenlet errors from
        lazy-loading outside an awaited context.
        """
        prop = await self.repo.create(data, owner.id)
        prop = await self.repo.get_by_id(prop.id)
        logger.info(
            "Property created",
            extra={"property_id": str(prop.id), "owner_id": str(owner.id)},
        )
        return PropertyRead.model_validate(prop)

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_for_owner(
        self,
        property_id: uuid.UUID,
        owner: User,
    ) -> PropertyRead:
        """Get own listing in any state (all fields, full address)."""
        prop = await self.repo.get_by_id_and_owner(property_id, owner.id)
        if prop is None:
            raise NotFoundException(
                message="Property not found",
                log_context={"property_id": str(property_id)},
            )
        return PropertyRead.model_validate(prop)

    async def get_public(
        self,
        property_id: uuid.UUID,
        requesting_user: User | None = None,
        has_confirmed_booking: bool = False,
    ) -> PropertyPublicRead:
        """
        Get a property for public display.

        address_hidden logic:
          - When False: full address shown to everyone.
          - When True: only city/state shown publicly.
            Exception: a user with a confirmed booking for this
            property sees the full address (has_confirmed_booking=True,
            set by the caller after checking the bookings table).

        Only PUBLISHED non-expired listings are visible publicly.
        Admin/moderators bypass this and can see any state.
        """
        prop = await self.repo.get_by_id(property_id)
        if prop is None:
            raise NotFoundException(message="Property not found")

        is_admin = requesting_user and requesting_user.role in (UserRole.ADMIN, UserRole.MODERATOR)

        if not is_admin and not prop.is_publicly_visible:
            raise NotFoundException(message="Property not found")

        show_full_address = not prop.address_hidden or has_confirmed_booking or is_admin

        return self._to_public_read(prop, show_full_address)

    async def list_for_owner(
        self,
        owner: User,
        page: int,
        per_page: int,
        approval_status: str | None = None,
    ) -> PaginatedResponse[PropertyCard]:
        """Agent's own listings dashboard."""
        props, total = await self.repo.list_for_owner(owner.id, page, per_page, approval_status)
        cards = [self._to_card(p) for p in props]
        return PaginatedResponse.paginate(cards, total, page, per_page)

    async def search(
        self,
        filters: PropertySearch,
    ) -> PaginatedResponse[PropertyCard]:
        """Public search — published + non-expired only."""
        props, total = await self.repo.search(filters)
        cards = [self._to_card(p) for p in props]
        return PaginatedResponse.paginate(cards, total, filters.page, filters.per_page)

    async def list_features(self) -> list[FeatureRead]:
        """Fetch all properties amenities classifications options."""
        features = await self.repo.list_features()
        return [FeatureRead.model_validate(f) for f in features]

    # ── Update ────────────────────────────────────────────────────────────────

    async def update(
        self,
        property_id: uuid.UUID,
        owner: User,
        data: PropertyUpdate,
    ) -> PropertyRead:
        """
        Update listing content. Only allowed in DRAFT or REJECTED states.
        A PUBLISHED listing must go through PENDING_REVIEW to be updated
        (submit_for_review() re-enters the approval workflow).
        """
        prop = await self.repo.get_by_id_and_owner(property_id, owner.id)
        if prop is None:
            raise NotFoundException(message="Property not found")

        if prop.approval_status not in _EDITABLE_STATES:
            raise ConflictException(
                message=(
                    f"Cannot edit a listing with status "
                    f"'{prop.approval_status.value}'. "
                    "Only draft or rejected listings can be edited directly."
                ),
                error_code="invalid_state_transition",
            )

        updated = await self.repo.update(prop, data)
        return PropertyRead.model_validate(updated)

    # ── State machine transitions ─────────────────────────────────────────────

    async def submit_for_review(
        self,
        property_id: uuid.UUID,
        agent: User,
    ) -> PropertyRead:
        """
        DRAFT | REJECTED | ARCHIVED | PUBLISHED → PENDING_REVIEW
        Resets rejection_reason on resubmission.
        """
        prop = await self.repo.get_by_id_and_owner(property_id, agent.id)
        if prop is None:
            raise NotFoundException(message="Property not found")

        allowed_from = {
            ApprovalStatus.DRAFT,
            ApprovalStatus.REJECTED,
            ApprovalStatus.ARCHIVED,
            ApprovalStatus.PUBLISHED,  # edit after publish
        }
        if prop.approval_status not in allowed_from:
            raise ConflictException(
                message=f"Cannot submit listing with status '{prop.approval_status.value}'",
                error_code="invalid_state_transition",
            )

        # Clear rejection reason when resubmitting
        prop.rejection_reason = None
        updated = await self.repo.update_status(prop, ApprovalStatus.PENDING_REVIEW)
        logger.info("Property submitted for review", extra={"property_id": str(property_id)})
        return PropertyRead.model_validate(updated)

    async def approve(
        self,
        property_id: uuid.UUID,
        admin: User,
    ) -> PropertyRead:
        """PENDING_REVIEW → APPROVED. Admin only."""
        prop = await self.repo.get_by_id(property_id)
        if prop is None:
            raise NotFoundException(message="Property not found")

        if prop.approval_status != ApprovalStatus.PENDING_REVIEW:
            raise ConflictException(
                message="Only pending_review listings can be approved",
                error_code="invalid_state_transition",
            )

        updated = await self.repo.update_status(prop, ApprovalStatus.APPROVED, approved_by=admin.id)
        logger.info(
            "Property approved", extra={"property_id": str(property_id), "admin_id": str(admin.id)}
        )
        return PropertyRead.model_validate(updated)

    async def reject(
        self,
        property_id: uuid.UUID,
        admin: User,
        reason: str,
    ) -> PropertyRead:
        """PENDING_REVIEW → REJECTED. Admin only. Reason required."""
        prop = await self.repo.get_by_id(property_id)
        if prop is None:
            raise NotFoundException(message="Property not found")

        if prop.approval_status != ApprovalStatus.PENDING_REVIEW:
            raise ConflictException(
                message="Only pending_review listings can be rejected",
                error_code="invalid_state_transition",
            )

        updated = await self.repo.update_status(
            prop, ApprovalStatus.REJECTED, rejection_reason=reason
        )
        logger.info(
            "Property rejected", extra={"property_id": str(property_id), "admin_id": str(admin.id)}
        )
        return PropertyRead.model_validate(updated)

    async def publish(
        self,
        property_id: uuid.UUID,
        agent: User,
    ) -> PropertyRead:
        """
        APPROVED → PUBLISHED. Sets expires_at = now + 30 days.
        """
        prop = await self.repo.get_by_id_and_owner(property_id, agent.id)
        if prop is None:
            raise NotFoundException(message="Property not found")

        if prop.approval_status != ApprovalStatus.APPROVED:
            raise ConflictException(
                message="Only approved listings can be published",
                error_code="invalid_state_transition",
            )

        prop.expires_at = datetime.now(tz=UTC) + timedelta(days=LISTING_EXPIRY_DAYS)
        updated = await self.repo.update_status(prop, ApprovalStatus.PUBLISHED)
        logger.info("Property published", extra={"property_id": str(property_id)})

        from app.tasks.celery_app import celery_app

        celery_app.send_task(
            "app.tasks.saved_search_alerts.notify_matching_saved_searches",
            args=[str(property_id)],
        )

        return PropertyRead.model_validate(updated)

    async def mark_reserved(self, property_id: uuid.UUID, agent: User) -> PropertyRead:
        """PUBLISHED → RESERVED."""
        return await self._transition(
            property_id,
            agent.id,
            from_states={ApprovalStatus.PUBLISHED},
            to_state=ApprovalStatus.RESERVED,
        )

    async def mark_rented(self, property_id: uuid.UUID, agent: User) -> PropertyRead:
        """PUBLISHED | RESERVED → RENTED. Stamps rented_at."""
        prop = await self._get_owned_or_raise(property_id, agent.id)
        allowed = {ApprovalStatus.PUBLISHED, ApprovalStatus.RESERVED}
        if prop.approval_status not in allowed:
            raise ConflictException(
                message="Only published or reserved listings can be marked as rented",
                error_code="invalid_state_transition",
            )
        prop.rented_at = datetime.now(tz=UTC)
        updated = await self.repo.update_status(prop, ApprovalStatus.RENTED)
        return PropertyRead.model_validate(updated)

    async def mark_sold(self, property_id: uuid.UUID, agent: User) -> PropertyRead:
        """PUBLISHED → SOLD."""
        return await self._transition(
            property_id,
            agent.id,
            from_states={ApprovalStatus.PUBLISHED},
            to_state=ApprovalStatus.SOLD,
        )

    async def archive(self, property_id: uuid.UUID, agent: User) -> PropertyRead:
        """PUBLISHED | RESERVED | RENTED | SOLD → ARCHIVED. Agent-initiated."""
        return await self._transition(
            property_id,
            agent.id,
            from_states={
                ApprovalStatus.PUBLISHED,
                ApprovalStatus.RESERVED,
                ApprovalStatus.RENTED,
                ApprovalStatus.SOLD,
            },
            to_state=ApprovalStatus.ARCHIVED,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_owned_or_raise(self, property_id: uuid.UUID, owner_id: uuid.UUID) -> Property:
        prop = await self.repo.get_by_id_and_owner(property_id, owner_id)
        if prop is None:
            raise NotFoundException(message="Property not found")
        return prop

    async def _transition(
        self,
        property_id: uuid.UUID,
        owner_id: uuid.UUID,
        from_states: set[ApprovalStatus],
        to_state: ApprovalStatus,
    ) -> PropertyRead:
        prop = await self._get_owned_or_raise(property_id, owner_id)
        if prop.approval_status not in from_states:
            allowed = " or ".join(s.value for s in from_states)
            raise ConflictException(
                message=f"Listing must be {allowed} to perform this action",
                error_code="invalid_state_transition",
            )
        updated = await self.repo.update_status(prop, to_state)
        return PropertyRead.model_validate(updated)

    def _to_card(self, prop: Property) -> PropertyCard:
        primary = prop.primary_image
        return PropertyCard(
            id=prop.id,
            title=prop.title,
            price=prop.price,
            currency=prop.currency,
            property_type=prop.property_type.value,
            status=prop.status.value,
            city=prop.city,
            state=prop.state,
            bedrooms=prop.bedrooms,
            featured=prop.featured,
            verified=prop.verified,
            primary_image_url=primary.display_url if primary else None,
            blurhash=primary.blurhash if primary else None,
        )

    def _to_public_read(self, prop: Property, show_full_address: bool) -> PropertyPublicRead:
        return PropertyPublicRead(
            id=prop.id,
            title=prop.title,
            description=prop.description,
            price=prop.price,
            currency=prop.currency,
            property_type=prop.property_type.value,
            status=prop.status.value,
            approval_status=prop.approval_status.value,
            public_address=prop.public_address,
            city=prop.city,
            state=prop.state,
            latitude=prop.latitude if show_full_address else None,
            longitude=prop.longitude if show_full_address else None,
            bedrooms=prop.bedrooms,
            bathrooms=prop.bathrooms,
            area_size=prop.area_size,
            featured=prop.featured,
            verified=prop.verified,
            features=[FeatureRead.model_validate(f) for f in prop.features],
            images=[PropertyImageRead.model_validate(img) for img in prop.images],
        )
