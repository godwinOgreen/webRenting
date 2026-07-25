"""
domains/messaging/router.py

HTTP layer for conversations and messages. No business logic.

Starting a conversation requires require_verified_renter() (Rule 3).
Reading/sending within an EXISTING conversation only requires
get_current_user — both participants (renter and agent) need to read
and reply in a thread they're already part of, and an agent is never
gated by the renter-side subscription+KYC requirement to use messaging
they didn't initiate.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, get_db
from app.domains.messaging.schemas import (
    ConversationDetailRead,
    ConversationRead,
    MessageCreate,
    MessageRead,
    StartConversationRequest,
)
from app.domains.messaging.service import MessagingService
from app.domains.users.models import User
from app.permissions.guards import require_verified_renter
from app.shared.schemas import CursorPage, PaginatedResponse, SuccessResponse

router = APIRouter(prefix="/conversations", tags=["messaging"])


# ── POST /conversations ───────────────────────────────────────────────────────

@router.post(
    "",
    response_model=SuccessResponse[ConversationDetailRead],
    status_code=201,
    summary="Start (or continue) a conversation about a property",
)
async def start_conversation(
    data: StartConversationRequest,
    current_user: User = Depends(require_verified_renter()),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[ConversationDetailRead]:
    """Rule 3: requires active subscription + verified KYC to initiate."""
    svc = MessagingService(db)
    return SuccessResponse.ok(
        data=await svc.start_conversation(current_user, data),
        message="Message sent",
    )


# ── GET /conversations ────────────────────────────────────────────────────────

@router.get(
    "",
    response_model=PaginatedResponse[ConversationRead],
    summary="List own conversations",
)
async def list_conversations(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ConversationRead]:
    svc = MessagingService(db)
    return await svc.list_conversations(current_user, page, per_page)


# ── GET /conversations/{id} ───────────────────────────────────────────────────

@router.get(
    "/{conversation_id}",
    response_model=SuccessResponse[ConversationDetailRead],
    summary="Get conversation details (participants)",
)
async def get_conversation(
    conversation_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[ConversationDetailRead]:
    svc = MessagingService(db)
    return SuccessResponse.ok(
        data=await svc.get_conversation(conversation_id, current_user)
    )


# ── GET /conversations/{id}/messages ──────────────────────────────────────────

@router.get(
    "/{conversation_id}/messages",
    response_model=CursorPage[MessageRead],
    summary="Get message history (cursor-paginated, newest first)",
)
async def list_messages(
    conversation_id: uuid.UUID,
    cursor: Optional[str] = Query(
        None, description="ISO timestamp of the oldest message already loaded"
    ),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[MessageRead]:
    """
    Opening this endpoint marks the conversation as read for the
    current user — see MessagingService.list_messages() docstring.
    """
    svc = MessagingService(db)
    return await svc.list_messages(conversation_id, current_user, cursor, limit)


# ── POST /conversations/{id}/messages ─────────────────────────────────────────

@router.post(
    "/{conversation_id}/messages",
    response_model=SuccessResponse[MessageRead],
    status_code=201,
    summary="Send a message in an existing conversation",
)
async def send_message(
    conversation_id: uuid.UUID,
    data: MessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SuccessResponse[MessageRead]:
    svc = MessagingService(db)
    return SuccessResponse.ok(
        data=await svc.send_message(conversation_id, current_user, data.body)
    )
