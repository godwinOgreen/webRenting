"""
domains/properties/schemas.py

Four views of a property with different exposure levels:

  PropertyCreate      — agent creating a new draft listing
  PropertyUpdate      — partial update (draft/rejected states only)
  PropertyRead        — full view for the owning agent or admin
  PropertyPublicRead  — public view, respects address_hidden
  PropertyCard        — lightweight card for search results grids
  PropertySearch      — query parameters for the search endpoint
  FeatureRead         — a single amenity/feature item
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_PROPERTY_TYPES = {"apartment", "house", "duplex", "studio", "commercial", "office"}
_LISTING_STATUSES = {"rent", "sale", "short_let", "lease"}


# ── Feature ───────────────────────────────────────────────────────────────────


class FeatureRead(BaseModel):
    """A single amenity/feature attached to a property (e.g. pool, parking)."""

    id: uuid.UUID
    name: str
    icon: str | None = None
    category: str

    model_config = ConfigDict(from_attributes=True)


# ── Image (embedded in property responses) ────────────────────────────────────


class PropertyImageRead(BaseModel):
    """Single image for a property listing, with ordering and primary flag."""

    id: uuid.UUID
    display_order: int
    is_primary: bool
    display_url: str | None = None
    blurhash: str | None = None

    model_config = ConfigDict(from_attributes=True)


# ── Create ────────────────────────────────────────────────────────────────────


class PropertyCreate(BaseModel):
    """Payload for creating a new property listing as an agent."""

    title: str = Field(..., min_length=5, max_length=255)
    description: str | None = None
    price: Decimal = Field(..., gt=0)
    currency: str = Field("NGN", max_length=3)
    property_type: str = Field(..., description="apartment|house|duplex|studio|commercial|office")
    status: str = Field(..., description="rent|sale|short_let|lease")
    # Address
    street: str | None = Field(None, max_length=255)
    building_number: str | None = Field(None, max_length=50)
    city: str = Field(..., min_length=1, max_length=100)
    state: str = Field(..., min_length=1, max_length=100)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    address_hidden: bool = False
    # Physical details
    bedrooms: int | None = Field(None, ge=0)
    bathrooms: int | None = Field(None, ge=0)
    master_bedrooms: int | None = Field(None, ge=0)
    toilets: int | None = Field(None, ge=0)
    area_size: Decimal | None = Field(None, gt=0)
    # Features to attach on creation (optional)
    feature_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="UUIDs of features from GET /properties/features",
    )

    model_config = ConfigDict(str_strip_whitespace=True)

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        from app.constants import SUPPORTED_CURRENCIES

        if v.upper() not in SUPPORTED_CURRENCIES:
            raise ValueError(f"Unsupported currency. Use one of: {SUPPORTED_CURRENCIES}")
        return v.upper()

    @field_validator("property_type")
    @classmethod
    def validate_property_type(cls, v: str) -> str:
        if v not in _PROPERTY_TYPES:
            raise ValueError(f"property_type must be one of: {_PROPERTY_TYPES}")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        if v not in _LISTING_STATUSES:
            raise ValueError(f"status must be one of: {_LISTING_STATUSES}")
        return v


# ── Update ────────────────────────────────────────────────────────────────────


class PropertyUpdate(BaseModel):
    """Partial update — only allowed in draft or rejected states.
    All fields optional; only supplied fields are changed.
    feature_ids replaces the full feature set if supplied.
    """

    title: str | None = Field(None, min_length=5, max_length=255)
    description: str | None = None
    price: Decimal | None = Field(None, gt=0)
    property_type: str | None = None
    status: str | None = None
    street: str | None = Field(None, max_length=255)
    building_number: str | None = Field(None, max_length=50)
    city: str | None = Field(None, min_length=1, max_length=100)
    state: str | None = Field(None, min_length=1, max_length=100)
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    address_hidden: bool | None = None
    bedrooms: int | None = Field(None, ge=0)
    bathrooms: int | None = Field(None, ge=0)
    master_bedrooms: int | None = Field(None, ge=0)
    toilets: int | None = Field(None, ge=0)
    area_size: Decimal | None = Field(None, gt=0)
    feature_ids: list[uuid.UUID] | None = None

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("property_type")
    @classmethod
    def validate_property_type(cls, v: str | None) -> str | None:
        if v is not None and v not in _PROPERTY_TYPES:
            raise ValueError(f"property_type must be one of: {_PROPERTY_TYPES}")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str | None) -> str | None:
        if v is not None and v not in _LISTING_STATUSES:
            raise ValueError(f"status must be one of: {_LISTING_STATUSES}")
        return v


class PropertyRejection(BaseModel):
    """Request payload for admin property rejection."""

    rejection_reason: str = Field(..., min_length=1, max_length=1000)

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ── Full read (owner / admin) ─────────────────────────────────────────────────


class PropertyRead(BaseModel):
    """
    Full listing view for the owning agent or admin.
    Includes address, coordinates, approval status, and rejection reason.
    """

    id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    description: str | None = None
    price: Decimal
    currency: str
    property_type: str
    status: str
    approval_status: str
    rejection_reason: str | None = None
    street: str | None = None
    building_number: str | None = None
    city: str
    state: str
    latitude: float
    longitude: float
    address_hidden: bool
    bedrooms: int | None = None
    bathrooms: int | None = None
    master_bedrooms: int | None = None
    toilets: int | None = None
    area_size: Decimal | None = None
    featured: bool
    verified: bool
    expires_at: datetime | None = None
    features: list[FeatureRead] = Field(default_factory=list)
    images: list[PropertyImageRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


# ── Public read (search results, listing page) ────────────────────────────────


class PropertyPublicRead(BaseModel):
    """
    Public view of a property listing.
    Hides coordinates when address_hidden is True — only city/state
    is exposed. Full address is revealed to users with a confirmed
    booking (handled by the service layer).
    """

    id: uuid.UUID
    title: str
    description: str | None = None
    price: Decimal
    currency: str
    property_type: str
    status: str
    approval_status: str
    public_address: str  # city/state or full address, built by service
    city: str
    state: str
    latitude: float | None = None
    longitude: float | None = None
    bedrooms: int | None = None
    bathrooms: int | None = None
    master_bedrooms: int | None = None
    area_size: Decimal | None = None
    featured: bool
    verified: bool
    features: list[FeatureRead] = Field(default_factory=list)
    images: list[PropertyImageRead] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


# ── Card (lightweight grid item) ──────────────────────────────────────────────


class PropertyCard(BaseModel):
    """Lightweight view for search result grids and map pins."""

    id: uuid.UUID
    title: str
    price: Decimal
    currency: str
    property_type: str
    status: str
    city: str
    state: str
    bedrooms: int | None = None
    featured: bool
    verified: bool
    primary_image_url: str | None = None
    blurhash: str | None = None

    model_config = ConfigDict(from_attributes=True)


# ── Search parameters ─────────────────────────────────────────────────────────


class PropertySearch(BaseModel):
    """
    Query parameters for GET /properties. All optional.
    no filters = return all published listings.
    """

    city: str | None = None
    state: str | None = None
    property_type: str | None = None
    listing_status: str | None = None
    min_price: Decimal | None = Field(None, gt=0)
    max_price: Decimal | None = Field(None, gt=0)
    min_bedrooms: int | None = Field(None, ge=0)
    max_bedrooms: int | None = Field(None, ge=0)
    min_bathrooms: int | None = Field(None, ge=0)
    featured_only: bool = False
    verified_only: bool = False
    # Geospatial — all three required together
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    radius_km: float | None = Field(None, gt=0, le=50)
    # Feature filters
    feature_ids: list[uuid.UUID] = Field(default_factory=list)
    # Pagination
    page: int = Field(1, ge=1)
    per_page: int = Field(20, ge=1, le=100)

    @field_validator("property_type")
    @classmethod
    def validate_property_type(cls, v: str | None) -> str | None:
        if v is not None and v not in _PROPERTY_TYPES:
            raise ValueError(f"property_type must be one of: {_PROPERTY_TYPES}")
        return v

    @field_validator("listing_status")
    @classmethod
    def validate_listing_status(cls, v: str | None) -> str | None:
        if v is not None and v not in _LISTING_STATUSES:
            raise ValueError(f"listing_status must be one of: {_LISTING_STATUSES}")
        return v

    @model_validator(mode="after")
    def validate_search_ranges(self) -> PropertySearch:
        """Price and bedroom min/max pairs must be valid ranges."""
        if (
            self.min_price is not None
            and self.max_price is not None
            and self.max_price < self.min_price
        ):
            raise ValueError("max_price cannot be less than min_price")

        if (
            self.min_bedrooms is not None
            and self.max_bedrooms is not None
            and self.max_bedrooms < self.min_bedrooms
        ):
            raise ValueError("max_bedrooms cannot be less than min_bedrooms")

        # Geospatial: all three or none
        geo_fields = (self.lat, self.lng, self.radius_km)
        provided_count = sum(1 for field in geo_fields if field is not None)

        if provided_count > 0 and provided_count < 3:
            raise ValueError(
                "To use geospatial filtration, "
                "you must supply all three parameters: lat, lng, and radius_km"
            )

        return self
