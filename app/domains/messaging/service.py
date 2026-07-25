"""
domains/messaging/service.py

Business logic for conversations and messages.

Messaging an agent requires require_verified_renter() (Rule 3: active
subscription + KYC) — enforced at the router layer via the guard, not
here. This service assumes the caller is already authorized to message;
it only handles conversation/message creation and the "find existing
or create new" logic.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenException, NotFoundException
from app.domains.messaging.models import Conversation
from app.domains.messaging.repository import MessagingRepository
from app.domains.messaging.schemas import (
    ConversationDetailRead,
    ConversationParticipantRead,
    ConversationRead,
    MessageRead,
    StartConversationRequest,
)
from app.domains.properties.models import Property
from app.domains.users.models import User
from app.domains.users.schemas import UserPublicRead
from app.shared.schemas import CursorPage, PaginatedResponse

logger = logging.getLogger(__name__)


class MessagingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.repo = MessagingRepository(db)

    async def _get_property(self, property_id):
        """Direct model query — see bookings/service.py's identical helper docstring."""
        result = await self.db.execute(select(Property).where(Property.id == property_id))
        return result.scalar_one_or_none()

    # ── Start / find conversation ─────────────────────────────────────────────

    async def start_conversation(
        self,
        renter: User,
        data: StartConversationRequest,
    ) -> ConversationDetailRead:
        """
        Find an existing conversation about this property between the
        renter and the property's agent, or create a new one. Always
        sends the supplied message as the first (or next) message in
        the thread.

        The recipient is derived from Property.owner_id — never
        supplied by the client (see schemas.py docstring).

        Raises:
            NotFoundException: property doesn't exist
            ForbiddenException: renter is trying to message themselves
                                 (i.e. an agent messaging about their
                                 own listing)
        """
        prop = await self._get_property(data.property_id)
        if prop is None:
            raise NotFoundException(message="Property not found")

        agent_id = prop.owner_id
        if agent_id == renter.id:
            raise ForbiddenException(
                message="You cannot message yourself about your own listing",
                error_code="cannot_message_self",
            )

        conversation = await self.repo.find_existing_conversation(
            property_id=data.property_id,
            user_a=renter.id,
            user_b=agent_id,
        )
        if conversation is None:
            conversation = await self.repo.create_conversation(
                property_id=data.property_id,
                participant_ids=[renter.id, agent_id],
            )
            logger.info(
                "Conversation started",
                extra={
                    "conversation_id": str(conversation.id),
                    "property_id": str(data.property_id),
                },
            )

        await self.repo.create_message(
            conversation_id=conversation.id,
            sender_id=renter.id,
            body=data.message,
        )

        # Re-fetch with full participant data for the response
        conversation = await self.repo.get_conversation(conversation.id)
        if conversation is None:
            raise NotFoundException(message="Conversation not found")
        return self._to_detail(conversation)

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_conversation(
        self, conversation_id: uuid.UUID, user: User
    ) -> ConversationDetailRead:
        """Raises ForbiddenException if user is not a participant."""
        conversation = await self.repo.get_conversation(conversation_id)
        if conversation is None:
            raise NotFoundException(message="Conversation not found")

        if conversation.get_participant(user.id) is None:
            raise ForbiddenException(
                message="You are not a participant in this conversation",
                error_code="not_a_participant",
            )

        return self._to_detail(conversation)

    async def list_conversations(
        self, user: User, page: int, per_page: int
    ) -> PaginatedResponse[ConversationRead]:
        conversations, total = await self.repo.list_for_user(user.id, page, per_page)
        items = [self._to_list_item(c, user.id) for c in conversations]
        return PaginatedResponse.paginate(items, total, page, per_page)

    async def list_messages(
        self,
        conversation_id: uuid.UUID,
        user: User,
        cursor: Optional[str],
        limit: int = 50,
    ) -> CursorPage[MessageRead]:
        """
        Cursor-paginated message history, newest-first. Also marks the
        conversation as read for this user as a side effect of opening it
        — matches typical chat UX (viewing a thread marks it read).
        """
        conversation = await self.repo.get_conversation(conversation_id)
        if conversation is None:
            raise NotFoundException(message="Conversation not found")

        participant = conversation.get_participant(user.id)
        if participant is None:
            raise ForbiddenException(
                message="You are not a participant in this conversation",
                error_code="not_a_participant",
            )

        messages = await self.repo.list_messages(conversation_id, cursor, limit)
        await self.repo.mark_read(participant)

        items = [MessageRead.model_validate(m) for m in messages]
        next_cursor = (
            messages[-1].created_at.isoformat() if len(messages) == limit else None
        )
        return CursorPage.of(items, next_cursor)

    # ── Send message ──────────────────────────────────────────────────────────

    async def send_message(
        self,
        conversation_id: uuid.UUID,
        sender: User,
        body: str,
    ) -> MessageRead:
        conversation = await self.repo.get_conversation(conversation_id)
        if conversation is None:
            raise NotFoundException(message="Conversation not found")

        if conversation.get_participant(sender.id) is None:
            raise ForbiddenException(
                message="You are not a participant in this conversation",
                error_code="not_a_participant",
            )

        message = await self.repo.create_message(conversation_id, sender.id, body)
        logger.info(
            "Message sent",
            extra={"conversation_id": str(conversation_id), "sender_id": str(sender.id)},
        )
        return MessageRead.model_validate(message)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _to_list_item(
        self, conversation: Conversation, requesting_user_id: uuid.UUID
    ) -> ConversationRead:
        other = next(
            (p.user for p in conversation.participants if p.user_id != requesting_user_id),
            None,
        )
        last_msg = conversation.last_message
        return ConversationRead(
            id=conversation.id,
            property_id=conversation.property_id,
            property_title=conversation.listing.title if conversation.listing else None,
            other_participant=UserPublicRead.model_validate(other) if other else None,
            last_message_preview=(last_msg.body[:100] if last_msg else None),
            last_message_at=(last_msg.created_at if last_msg else None),
            unread_count=conversation.unread_count_for(requesting_user_id),
        )

    def _to_detail(self, conversation: Conversation) -> ConversationDetailRead:
        return ConversationDetailRead(
            id=conversation.id,
            property_id=conversation.property_id,
            participants=[
                ConversationParticipantRead(
                    user=UserPublicRead.model_validate(p.user),
                    last_read_at=p.last_read_at,
                )
                for p in conversation.participants
            ],
        )