"""
domains/media/service.py

Business logic for media upload and property image management.

Upload flow (see schemas.py module docstring for the two-step design):
  1. upload_file(): saves raw bytes to storage backend, creates
     MediaAsset row (status=pending), enqueues the media_processing
     Celery task, returns immediately.
  2. The Celery task (app/tasks/media_processing.py)
     calls back into MediaRepository.update_processing_result() and
     update_moderation_result() to advance the asset to ready/approved.

This service does NOT call the Celery task directly via a Python
import — it enqueues by task name through the Celery app instance
(app.tasks.celery_app), which is the standard decoupling: the web
process and the worker process don't need to import each other's
domain-specific task modules, only the shared Celery app config.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import (
    ALLOWED_IMAGE_TYPES,
    MAX_FILE_SIZE_KB,
    MAX_PROPERTY_IMAGES,
)
from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.domains.media.repository import MediaRepository
from app.domains.media.schemas import (
    AttachImageRequest,
    MediaAssetRead,
    PropertyImageRead,
    UploadResponse,
    VirtualTourCreate,
    VirtualTourRead,
)
from app.domains.properties.models import Property
from app.domains.users.models import User

logger = logging.getLogger(__name__)

MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_KB * 1024


class MediaService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = MediaRepository(db)

    async def _get_property_for_owner(
        self, property_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Property | None:
        """Direct model query — ensures property belongs to requesting owner."""
        result = await self.db.execute(
            select(Property).where(Property.id == property_id, Property.owner_id == owner_id)
        )
        return result.scalar_one_or_none()

    # ── Upload ────────────────────────────────────────────────────────────────

    async def upload_file(
        self,
        uploader: User,
        file_bytes: bytes,
        filename: str,
        mime_type: str,
    ) -> UploadResponse:
        """
        Validates the upload, persists raw bytes to the configured
        storage backend, creates the MediaAsset row, and enqueues
        processing. Does not block on processing.

        Raises:
            ValidationException: disallowed mime type or oversized file
        """
        if mime_type not in ALLOWED_IMAGE_TYPES:
            raise ValidationException(
                message="Unsupported file type",
                errors={"file": [f"Allowed types: {sorted(ALLOWED_IMAGE_TYPES)}"]},
            )
        if len(file_bytes) > MAX_FILE_SIZE_BYTES:
            raise ValidationException(
                message="File too large",
                errors={"file": [f"Maximum size is {MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB"]},
            )

        # Storage backend abstraction — local in dev/tests, S3 in prod.
        from app.integrations.storage.manager import get_storage

        storage = get_storage()
        original_url = await storage.save(file_bytes, filename, mime_type)

        asset = await self.repo.create_asset(
            uploaded_by=uploader.id,
            original_url=original_url,
            mime_type=mime_type,
            file_size_kb=len(file_bytes) // 1024,
        )

        # Enqueue async processing decoupled via Celery app
        from app.tasks.celery_app import celery_app

        celery_app.send_task(
            "app.tasks.media_processing.process_media_asset",
            args=[str(asset.id)],
        )

        logger.info(
            "Media uploaded, processing enqueued",
            extra={
                "media_asset_id": str(asset.id),
                "uploader_id": str(uploader.id),
            },
        )

        return UploadResponse(media_asset_id=asset.id, status=asset.status)

    async def get_asset(self, asset_id: uuid.UUID) -> MediaAssetRead:
        asset = await self.repo.get_asset_by_id(asset_id)
        if asset is None:
            raise NotFoundException(message="Media asset not found")
        return MediaAssetRead.model_validate(asset)

    # ── Property images ───────────────────────────────────────────────────────

    async def attach_image(
        self,
        property_id: uuid.UUID,
        owner: User,
        data: AttachImageRequest,
    ) -> PropertyImageRead:
        """
        Links a MediaAsset to a property. Verifies:
          - the property belongs to the requesting agent
          - the MediaAsset exists and was uploaded by owner
          - MAX_PROPERTY_IMAGES is not exceeded

        Automatically promotes first image to primary.
        """
        prop = await self._get_property_for_owner(property_id, owner.id)
        if prop is None:
            raise NotFoundException(message="Property not found")

        asset = await self.repo.get_asset_by_id(data.media_asset_id)
        if asset is None:
            raise NotFoundException(message="Media asset not found")
        if asset.uploaded_by != owner.id:
            raise ForbiddenException(
                message="You can only attach images you uploaded",
                error_code="not_asset_owner",
            )

        existing_count = await self.repo.count_for_property(property_id)
        if existing_count >= MAX_PROPERTY_IMAGES:
            raise ConflictException(
                message=f"Maximum of {MAX_PROPERTY_IMAGES} images per listing",
                error_code="max_images_reached",
            )

        # Auto-promote to primary if this is the first image attached
        is_primary = data.is_primary or (existing_count == 0)

        image = await self.repo.attach_image(
            property_id=property_id,
            media_asset_id=data.media_asset_id,
            display_order=data.display_order,
            is_primary=is_primary,
        )

        logger.info(
            "Image attached to property",
            extra={
                "property_id": str(property_id),
                "media_asset_id": str(data.media_asset_id),
            },
        )
        return PropertyImageRead.model_validate(image)

    async def list_images(self, property_id: uuid.UUID) -> list[PropertyImageRead]:
        images = await self.repo.list_for_property(property_id)
        return [PropertyImageRead.model_validate(img) for img in images]

    async def reorder_images(
        self,
        property_id: uuid.UUID,
        owner: User,
        image_ids_in_order: list[uuid.UUID],
    ) -> list[PropertyImageRead]:
        prop = await self._get_property_for_owner(property_id, owner.id)
        if prop is None:
            raise NotFoundException(message="Property not found")

        reordered = await self.repo.reorder_images(property_id, image_ids_in_order)
        return [PropertyImageRead.model_validate(img) for img in reordered]

    async def delete_image(self, property_id: uuid.UUID, image_id: uuid.UUID, owner: User) -> None:
        prop = await self._get_property_for_owner(property_id, owner.id)
        if prop is None:
            raise NotFoundException(message="Property not found")

        image = await self.repo.get_image_by_id(image_id)
        if image is None or image.property_id != property_id:
            raise NotFoundException(message="Image not found")

        was_primary = image.is_primary
        await self.repo.delete_image(image)

        # If primary image was deleted, promote next remaining image to primary
        if was_primary:
            remaining_images = await self.repo.list_for_property(property_id)
            if remaining_images:
                await self.repo.set_primary_image(property_id, remaining_images[0].id)

    # ── Virtual tours ─────────────────────────────────────────────────────────

    async def add_tour(
        self,
        property_id: uuid.UUID,
        owner: User,
        data: VirtualTourCreate,
    ) -> VirtualTourRead:
        prop = await self._get_property_for_owner(property_id, owner.id)
        if prop is None:
            raise NotFoundException(message="Property not found")

        tour = await self.repo.create_tour(property_id, str(data.url), data.display_order)
        return VirtualTourRead.model_validate(tour)

    async def list_tours(self, property_id: uuid.UUID) -> list[VirtualTourRead]:
        tours = await self.repo.list_tours_for_property(property_id)
        return [VirtualTourRead.model_validate(t) for t in tours]

    async def delete_tour(self, property_id: uuid.UUID, tour_id: uuid.UUID, owner: User) -> None:
        prop = await self._get_property_for_owner(property_id, owner.id)
        if prop is None:
            raise NotFoundException(message="Property not found")

        tour = await self.repo.get_tour_by_id(tour_id)
        if tour is None or tour.property_id != property_id:
            raise NotFoundException(message="Virtual tour not found")

        await self.repo.delete_tour(tour)
