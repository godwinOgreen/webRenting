"""
domains/media/router.py

HTTP layer for media upload and property image management.

Upload uses FastAPI's UploadFile for multipart form data — the only
domain in the platform that accepts non-JSON request bodies.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.core.exceptions import ValidationException
from app.domains.media.schemas import (
    AttachImageRequest,
    MediaAssetRead,
    PropertyImageRead,
    PropertyImageReorderRequest,
    UploadResponse,
    VirtualTourCreate,
    VirtualTourRead,
)
from app.domains.media.service import MediaService
from app.domains.users.models import User
from app.permissions.guards import require_agent
from app.shared.schemas import SuccessResponse

router = APIRouter(tags=["media"])


# ── POST /media/upload ────────────────────────────────────────────────────────


@router.post(
    "/media/upload",
    response_model=SuccessResponse[UploadResponse],
    status_code=202,  # Accepted — processing is async, not yet complete
    summary="Upload an image file (agent only)",
)
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[UploadResponse]:
    """
    202 Accepted rather than 201 Created — the MediaAsset row is
    created synchronously, but the resource is not yet fully "created"
    in the sense of being servable. 202 correctly signals "request
    accepted, processing continues asynchronously."
    """
    if file.content_type is None:
        raise ValidationException(
            message="File content type could not be determined",
        )
    file_bytes = await file.read()

    svc = MediaService(db)
    result = await svc.upload_file(
        uploader=current_user,
        file_bytes=file_bytes,
        filename=file.filename or "upload",
        mime_type=file.content_type,
    )
    return SuccessResponse.ok(data=result, message="Upload received")


# ── GET /media/{id} ────────────────────────────────────────────────────────────


@router.get(
    "/media/{asset_id}",
    response_model=SuccessResponse[MediaAssetRead],
    summary="Get media asset processing status",
)
async def get_asset(
    asset_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[MediaAssetRead]:
    """
    Used by the client to poll for processing completion after upload.
    No auth required — asset IDs are UUIDs (unguessable) and this only
    exposes processing status + a URL, not sensitive data.
    """
    svc = MediaService(db)
    return SuccessResponse.ok(data=await svc.get_asset(asset_id))


# ── Property Image Management ─────────────────────────────────────────────────


@router.get(
    "/properties/{property_id}/images",
    response_model=SuccessResponse[list[PropertyImageRead]],
    summary="List images attached to a property (public)",
)
async def list_property_images(
    property_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[list[PropertyImageRead]]:
    svc = MediaService(db)
    return SuccessResponse.ok(data=await svc.list_images(property_id))


@router.post(
    "/properties/{property_id}/images",
    response_model=SuccessResponse[PropertyImageRead],
    status_code=201,
    summary="Attach an uploaded image to a property (agent only)",
)
async def attach_image(
    property_id: uuid.UUID,
    data: AttachImageRequest,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[PropertyImageRead]:
    svc = MediaService(db)
    result = await svc.attach_image(property_id, current_user, data)
    return SuccessResponse.ok(data=result, message="Image attached successfully")


@router.patch(
    "/properties/{property_id}/images/reorder",
    response_model=SuccessResponse[list[PropertyImageRead]],
    summary="Reorder property images (agent only)",
)
async def reorder_images(
    property_id: uuid.UUID,
    data: PropertyImageReorderRequest,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[list[PropertyImageRead]]:
    svc = MediaService(db)
    reordered_images = await svc.reorder_images(property_id, current_user, data.image_ids_in_order)
    return SuccessResponse.ok(data=reordered_images, message="Images reordered successfully")


@router.delete(
    "/properties/{property_id}/images/{image_id}",
    response_model=SuccessResponse,
    summary="Remove an image from a property (agent only)",
)
async def delete_image(
    property_id: uuid.UUID,
    image_id: uuid.UUID,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    svc = MediaService(db)
    await svc.delete_image(property_id, image_id, current_user)
    return SuccessResponse.empty("Image removed")


# ── Virtual Tours ──────────────────────────────────────────────────────────────


@router.post(
    "/properties/{property_id}/virtual-tours",
    response_model=SuccessResponse[VirtualTourRead],
    status_code=201,
    summary="Add a virtual tour link (agent only)",
)
async def add_tour(
    property_id: uuid.UUID,
    data: VirtualTourCreate,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[VirtualTourRead]:
    svc = MediaService(db)
    return SuccessResponse.ok(
        data=await svc.add_tour(property_id, current_user, data),
        message="Virtual tour added",
    )


@router.get(
    "/properties/{property_id}/virtual-tours",
    response_model=SuccessResponse[list[VirtualTourRead]],
    summary="List virtual tours for a property (public)",
)
async def list_tours(
    property_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[list[VirtualTourRead]]:
    svc = MediaService(db)
    return SuccessResponse.ok(data=await svc.list_tours(property_id))


@router.delete(
    "/properties/{property_id}/virtual-tours/{tour_id}",
    response_model=SuccessResponse,
    summary="Remove a virtual tour (agent only)",
)
async def delete_tour(
    property_id: uuid.UUID,
    tour_id: uuid.UUID,
    current_user: User = Depends(require_agent()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse:
    svc = MediaService(db)
    await svc.delete_tour(property_id, tour_id, current_user)
    return SuccessResponse.empty("Virtual tour removed")
