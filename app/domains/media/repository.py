"""
domains/media/repository.py

Data access for media assets, property images, and virtual tours.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.domains.media.models import (
    MediaAsset,
    MediaStatus,
    ModerationStatus,
    PropertyImage,
    VirtualTour,
)


class MediaRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── MediaAsset ────────────────────────────────────────────────────────────

    async def get_asset_by_id(self, asset_id: uuid.UUID) -> MediaAsset | None:
        return await self.db.get(MediaAsset, asset_id)

    async def create_asset(
        self,
        uploaded_by: uuid.UUID,
        original_url: str,
        mime_type: str,
        file_size_kb: int | None = None,
    ) -> MediaAsset:
        """
        Creates the asset row in PENDING/PENDING state. Processing
        fields (optimized_url, thumbnail_url, blurhash, width, height)
        are filled in later by the Celery media_processing task via
        update_processing_result().
        """
        asset = MediaAsset(
            uploaded_by=uploaded_by,
            original_url=original_url,
            mime_type=mime_type,
            file_size_kb=file_size_kb,
        )
        self.db.add(asset)
        await self.db.flush()
        return asset

    async def update_processing_result(
        self,
        asset: MediaAsset,
        *,
        optimized_url: str | None = None,
        thumbnail_url: str | None = None,
        blurhash: str | None = None,
        width: int | None = None,
        height: int | None = None,
        status: MediaStatus | None = None,
    ) -> MediaAsset:
        """Used exclusively by the Celery media_processing task."""
        if optimized_url is not None:
            asset.optimized_url = optimized_url
        if thumbnail_url is not None:
            asset.thumbnail_url = thumbnail_url
        if blurhash is not None:
            asset.blurhash = blurhash
        if width is not None:
            asset.width = width
        if height is not None:
            asset.height = height
        if status is not None:
            asset.status = status

        await self.db.flush()
        return asset

    async def update_moderation_result(
        self, asset: MediaAsset, moderation_status: ModerationStatus
    ) -> MediaAsset:
        """Used exclusively by the Celery media_processing task (Rule 11)."""
        asset.moderation_status = moderation_status
        await self.db.flush()
        return asset

    # ── PropertyImage ─────────────────────────────────────────────────────────

    async def get_image_by_id(self, image_id: uuid.UUID) -> PropertyImage | None:
        result = await self.db.execute(
            select(PropertyImage)
            .options(joinedload(PropertyImage.media_asset))
            .where(PropertyImage.id == image_id)
        )
        return result.scalar_one_or_none()

    async def list_for_property(self, property_id: uuid.UUID) -> Sequence[PropertyImage]:
        result = await self.db.execute(
            select(PropertyImage)
            .options(joinedload(PropertyImage.media_asset))
            .where(PropertyImage.property_id == property_id)
            .order_by(PropertyImage.display_order)
        )
        return result.scalars().all()

    async def count_for_property(self, property_id: uuid.UUID) -> int:
        """Used to enforce MAX_PROPERTY_IMAGES before attaching a new one."""
        result = await self.db.execute(
            select(func.count())
            .select_from(PropertyImage)
            .where(PropertyImage.property_id == property_id)
        )
        return result.scalar_one()

    async def attach_image(
        self,
        property_id: uuid.UUID,
        media_asset_id: uuid.UUID,
        display_order: int,
        is_primary: bool,
    ) -> PropertyImage:
        if is_primary:
            await self._clear_primary(property_id)

        image = PropertyImage(
            property_id=property_id,
            media_asset_id=media_asset_id,
            display_order=display_order,
            is_primary=is_primary,
        )
        self.db.add(image)
        await self.db.flush()
        return image

    async def set_primary_image(
        self, property_id: uuid.UUID, image_id: uuid.UUID
    ) -> PropertyImage | None:
        """Sets target image to primary and unsets all other primary images for the property."""
        await self._clear_primary(property_id)

        image = await self.get_image_by_id(image_id)
        if image and image.property_id == property_id:
            image.is_primary = True
            await self.db.flush()
            return image
        return None

    async def reorder_images(
        self, property_id: uuid.UUID, image_ids_in_order: list[uuid.UUID]
    ) -> Sequence[PropertyImage]:
        images = await self.list_for_property(property_id)
        images_by_id = {img.id: img for img in images}

        for position, image_id in enumerate(image_ids_in_order):
            img = images_by_id.get(image_id)
            if img is not None:
                img.display_order = position

        await self.db.flush()
        return await self.list_for_property(property_id)

    async def delete_image(self, image: PropertyImage) -> None:
        await self.db.delete(image)
        await self.db.flush()

    async def _clear_primary(self, property_id: uuid.UUID) -> None:
        """Atomic bulk update to remove primary flag from all property images."""
        await self.db.execute(
            update(PropertyImage)
            .where(
                PropertyImage.property_id == property_id,
                PropertyImage.is_primary.is_(True),
            )
            .values(is_primary=False)
        )
        await self.db.flush()

    # ── VirtualTour ───────────────────────────────────────────────────────────

    async def get_tour_by_id(self, tour_id: uuid.UUID) -> VirtualTour | None:
        """Direct lookup instead of fetching all tours for a property."""
        return await self.db.get(VirtualTour, tour_id)

    async def list_tours_for_property(self, property_id: uuid.UUID) -> Sequence[VirtualTour]:
        result = await self.db.execute(
            select(VirtualTour)
            .where(VirtualTour.property_id == property_id)
            .order_by(VirtualTour.display_order)
        )
        return result.scalars().all()

    async def create_tour(
        self, property_id: uuid.UUID, url: str, display_order: int
    ) -> VirtualTour:
        tour = VirtualTour(property_id=property_id, url=url, display_order=display_order)
        self.db.add(tour)
        await self.db.flush()
        return tour

    async def delete_tour(self, tour: VirtualTour) -> None:
        await self.db.delete(tour)
        await self.db.flush()
