"""
domains/messaging/schemas.py

Request/response schemas for conversations and messages.

Cursor-based pagination is used for the message thread (CursorPage,
from shared/schemas.py) — message history can grow large and infinite
scroll is the natural UX, unlike page-numbered lists elsewhere.
Conversation list uses standard PaginatedResponse — there are far fewer
conversations than messages per user, page numbers are fine there.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domains.users.schemas import UserPublicRead

# ── Start conversation ────────────────────────────────────────────────────────


class StartConversationRequest(BaseModel):
    """
    Renter starts a conversation with the agent who owns property_id.
    The recipient (agent) is derived server-side from the property's
    owner_id, not supplied by the client — prevents messaging an
    arbitrary user under the guise of "about this property."
    """

    property_id: uuid.UUID
    message: str = Field(..., min_length=1, max_length=5000)


# ── Message ────────────────────────────────────────────────────────────────────


class MessageCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=5000)


class MessageRead(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    sender_id: uuid.UUID
    body: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Conversation ──────────────────────────────────────────────────────────────


class ConversationParticipantRead(BaseModel):
    user: UserPublicRead
    last_read_at: datetime | None = None


class ConversationRead(BaseModel):
    """
    Conversation list item — shows the other participant, last message
    preview, and unread count. Built by the service from the
    Conversation model's computed properties (last_message,
    unread_count_for), not raw column mapping.
    """

    id: uuid.UUID
    property_id: uuid.UUID | None = None
    property_title: str | None = None
    other_participant: UserPublicRead | None = None
    last_message_preview: str | None = None
    last_message_at: datetime | None = None
    unread_count: int


class ConversationDetailRead(BaseModel):
    """Full conversation view, used when opening a thread."""

    id: uuid.UUID
    property_id: uuid.UUID | None = None
    participants: list[ConversationParticipantRead]
