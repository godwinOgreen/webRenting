"""
domains/properties/repository.py

Data access for the properties domain. Includes PostGIS spatial queries
for radius-based property search.

PostGIS approach: latitude/longitude are stored as DOUBLE PRECISION columns
(not a Geometry column). Spatial operations are performed by calling
PostgreSQL functions (ST_DWithin, ST_MakePoint, ST_SetSRID) via
SQLAlchemy's func interface — no geoalchemy2 required.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.properties.models import (
    ApprovalStatus,
    Property,
    PropertyFeature,
    PropertyFeatureMap,
)
from app.domains.properties.schemas import PropertyCreate, PropertySearch, PropertyUpdate


class PropertyRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Reads ─────────────────────────────────────────────────────────────────

    async def get_by_id(self, property_id: uuid.UUID) -> Property | None:
        """Fetch a single property with features and images eagerly loaded."""
        result = await self.db.execute(
            select(Property)
            .where(Property.id == property_id)
            .options(
                selectinload(Property.features),
                selectinload(Property.images),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_id_and_owner(
        self, property_id: uuid.UUID, owner_id: uuid.UUID
    ) -> Property | None:
        """Fetch a property only if the requesting user is the owner."""
        result = await self.db.execute(
            select(Property)
            .where(Property.id == property_id, Property.owner_id == owner_id)
            .options(
                selectinload(Property.features),
                selectinload(Property.images),
            )
        )
        return result.scalar_one_or_none()

    async def list_for_owner(
        self,
        owner_id: uuid.UUID,
        page: int,
        per_page: int,
        approval_status: str | None = None,
    ) -> tuple[list[Property], int]:
        """Agent's own listings, all statuses. Returns (items, total)."""
        base = select(Property).where(Property.owner_id == owner_id)
        if approval_status:
            base = base.where(Property.approval_status == approval_status)

        count_q = select(func.count()).select_from(base.subquery())
        data_q = (
            base.options(selectinload(Property.images))
            .order_by(Property.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )

        count_res = await self.db.execute(count_q)
        data_res = await self.db.execute(data_q)

        return list(data_res.scalars().all()), count_res.scalar_one()

    async def search(self, f: PropertySearch) -> tuple[list[Property], int]:
        """
        Public search — only published, non-expired listings.

        PostGIS radius search uses ST_DWithin on Geography type for
        accurate meter-based distance regardless of latitude.
        """
        base = select(Property).where(
            Property.approval_status == ApprovalStatus.PUBLISHED,
            Property.expires_at > func.now(),
        )

        # String / enum filters
        if f.city:
            base = base.where(func.lower(Property.city) == f.city.lower())
        if f.state:
            base = base.where(func.lower(Property.state) == f.state.lower())
        if f.property_type:
            base = base.where(Property.property_type == f.property_type)
        if f.listing_status:
            base = base.where(Property.status == f.listing_status)

        # Numeric range filters
        if f.min_price is not None:
            base = base.where(Property.price >= f.min_price)
        if f.max_price is not None:
            base = base.where(Property.price <= f.max_price)
        if f.min_bedrooms is not None:
            base = base.where(Property.bedrooms >= f.min_bedrooms)
        if f.max_bedrooms is not None:
            base = base.where(Property.bedrooms <= f.max_bedrooms)
        if f.min_bathrooms is not None:
            base = base.where(Property.bathrooms >= f.min_bathrooms)

        # Boolean flags
        if f.featured_only:
            base = base.where(Property.featured.is_(True))
        if f.verified_only:
            base = base.where(Property.verified.is_(True))

        # PostGIS radius filter
        if f.lat is not None and f.lng is not None and f.radius_km is not None:
            # Build the search point as a geography object
            search_point = func.ST_SetSRID(func.ST_MakePoint(f.lng, f.lat), 4326).cast("geography")

            # Build the property point as a geography object
            property_point = func.ST_SetSRID(
                func.ST_MakePoint(Property.longitude, Property.latitude), 4326
            ).cast("geography")

            base = base.where(
                func.ST_DWithin(property_point, search_point, f.radius_km * 1000)  # km → metres
            )

        # Must have ALL requested features (single subquery, not a loop)
        if f.feature_ids:
            num_features = len(f.feature_ids)
            feature_subquery = (
                select(PropertyFeatureMap.property_id)
                .where(PropertyFeatureMap.feature_id.in_(f.feature_ids))
                .group_by(PropertyFeatureMap.property_id)
                .having(func.count(PropertyFeatureMap.feature_id) == num_features)
                .subquery()
            )
            base = base.join(
                feature_subquery,
                Property.id == feature_subquery.c.property_id,
            ).distinct()

        # Count and fetch
        count_q = select(func.count()).select_from(base.subquery())
        data_q = (
            base.options(
                selectinload(Property.images),
                selectinload(Property.features),
            )
            .order_by(
                Property.featured.desc(),
                Property.created_at.desc(),
            )
            .offset((f.page - 1) * f.per_page)
            .limit(f.per_page)
        )

        count_res = await self.db.execute(count_q)
        data_res = await self.db.execute(data_q)
        return list(data_res.scalars().all()), count_res.scalar_one()

    async def list_features(self) -> list[PropertyFeature]:
        """All available amenity/feature options (seed data)."""
        result = await self.db.execute(
            select(PropertyFeature).order_by(PropertyFeature.category, PropertyFeature.name)
        )
        return list(result.scalars().all())

    # ── Writes ────────────────────────────────────────────────────────────────

    async def create(
        self,
        data: PropertyCreate,
        owner_id: uuid.UUID,
    ) -> Property:
        """Create a new property listing in DRAFT state."""
        prop = Property(
            owner_id=owner_id,
            title=data.title,
            description=data.description,
            price=data.price,
            currency=data.currency,
            property_type=data.property_type,
            status=data.status,
            street=data.street,
            building_number=data.building_number,
            city=data.city,
            state=data.state,
            latitude=data.latitude,
            longitude=data.longitude,
            address_hidden=data.address_hidden,
            bedrooms=data.bedrooms,
            bathrooms=data.bathrooms,
            master_bedrooms=data.master_bedrooms,
            toilets=data.toilets,
            area_size=data.area_size,
        )
        self.db.add(prop)
        await self.db.flush()

        if data.feature_ids:
            await self._set_features(prop.id, data.feature_ids)

        return prop

    async def update(self, prop: Property, data: PropertyUpdate) -> Property:
        """Apply partial update. feature_ids replaces the full set if supplied."""
        update_fields = data.model_dump(exclude_none=True, exclude={"feature_ids"})
        for field, value in update_fields.items():
            setattr(prop, field, value)

        if data.feature_ids is not None:
            await self._set_features(prop.id, data.feature_ids)

        await self.db.flush()
        return prop

    async def update_status(
        self,
        prop: Property,
        new_status: ApprovalStatus,
        *,
        rejection_reason: str | None = None,
        approved_by: uuid.UUID | None = None,
    ) -> Property:
        """Update approval status and optional side-effect fields."""
        prop.approval_status = new_status
        if rejection_reason is not None:
            prop.rejection_reason = rejection_reason
        if approved_by is not None:
            prop.approved_by = approved_by
        await self.db.flush()
        return prop

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _set_features(
        self,
        property_id: uuid.UUID,
        feature_ids: list[uuid.UUID],
    ) -> None:
        """Replace all features for a property. Delete existing, insert new."""
        await self.db.execute(
            delete(PropertyFeatureMap).where(PropertyFeatureMap.property_id == property_id)
        )
        if feature_ids:
            maps = [
                PropertyFeatureMap(property_id=property_id, feature_id=fid) for fid in feature_ids
            ]
            self.db.add_all(maps)
        await self.db.flush()
