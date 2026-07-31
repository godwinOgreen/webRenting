"""
domains/media/schemas.py

Request/response schemas for media upload and processing.

Upload is a two-step flow:
  1. POST /media/upload — multipart file upload, creates a MediaAsset
     row with status=pending, kicks off the Celery media_processing
     task, returns the MediaAsset id immediately (does NOT block on
     processing — compression/thumbnailing/moderation happen async).
  2. Client polls GET /media/{id} (or listens via the property's
     images array) until status flips to ready and moderation_status
     to approved, at which point display_url is populated.

There is no MediaAssetCreate schema with raw fields (url, mime_type
typed in by the client) — the actual file bytes are the input, and
the row is constructed server-side from the multipart upload + storage
backend response. See media/service.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    PlainSerializer,
    UrlConstraints,
)

from app.domains.media.models import MediaStatus, ModerationStatus

# Serializes HttpUrl objects directly to str in API JSON responses
HttpUrlString = Annotated[
    HttpUrl,
    UrlConstraints(max_length=512, allowed_schemes=["http", "https"]),
    PlainSerializer(lambda v: str(v), return_type=str),
]


# ── Media asset ───────────────────────────────────────────────────────────────


class MediaAssetRead(BaseModel):
    """Read representation of a media asset with processing status."""

    id: uuid.UUID
    status: MediaStatus
    moderation_status: ModerationStatus
    display_url: str | None = None
    thumbnail_url: str | None = None
    blurhash: str | None = None
    width: int | None = None
    height: int | None = None
    mime_type: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UploadResponse(BaseModel):
    """
    Returned immediately after upload — before processing completes.
    is_servable will be False at this point; the client should poll or
    listen for the asset to become ready.
    """

    media_asset_id: uuid.UUID
    status: MediaStatus
    message: str = "Upload received, processing in progress"


# ── Property images (linking a MediaAsset to a property) ─────────────────────


class AttachImageRequest(BaseModel):
    """
    Links an already-uploaded (or still-processing) MediaAsset to a
    property as a PropertyImage. Separated from the upload step itself
    because a single MediaAsset upload and its attachment to a specific
    property/display position are different concerns — e.g. a future
    "upload once, use across multiple listings" feature would reuse the
    same MediaAsset with different AttachImageRequest calls.
    """

    media_asset_id: uuid.UUID
    display_order: int = Field(0, ge=0)
    is_primary: bool = False


class PropertyImageRead(BaseModel):
    """Read representation of a property image link."""

    id: uuid.UUID
    property_id: uuid.UUID
    media_asset_id: uuid.UUID
    display_order: int
    is_primary: bool
    created_at: datetime
    is_visible: bool
    display_url: str | None = None
    thumbnail_url: str | None = None
    media_asset: MediaAssetRead | None = None

    model_config = ConfigDict(from_attributes=True)


class PropertyImageReorderRequest(BaseModel):
    """
    Bulk reorder — client sends the full ordered list of image IDs
    after a drag-and-drop reorder in the UI. Simpler and less
    error-prone than N individual "move this one to position X" calls.
    """

    image_ids_in_order: list[uuid.UUID] = Field(..., min_length=1)


# ── Virtual tours ──────────────────────────────────────────────────────────────


class VirtualTourCreate(BaseModel):
    """Request to add a virtual tour to a property."""

    url: HttpUrlString
    display_order: int = Field(0, ge=0)


class VirtualTourRead(BaseModel):
    """Read representation of a virtual tour."""

    id: uuid.UUID
    property_id: uuid.UUID
    url: str
    display_order: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
