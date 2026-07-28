"""
domains/messaging/repository.py

Data access for conversations and messages.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domains.messaging.models import (
    Conversation,
    ConversationParticipant,
    Message,
)


class MessagingRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ── Conversation reads ────────────────────────────────────────────────────

    async def get_conversation(self, conversation_id: uuid.UUID) -> Conversation | None:
        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.id == conversation_id)
            .options(
                selectinload(Conversation.participants).selectinload(ConversationParticipant.user),
                selectinload(Conversation.messages),
            )
        )
        return result.scalar_one_or_none()

    async def get_participant(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID
    ) -> ConversationParticipant | None:
        result = await self.db.execute(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == conversation_id,
                ConversationParticipant.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def find_existing_conversation(
        self,
        property_id: uuid.UUID,
        user_a: uuid.UUID,
        user_b: uuid.UUID,
    ) -> Conversation | None:
        """
        Find an existing conversation about a property between exactly
        these two users — used so re-messaging an agent about the same
        property reuses the thread instead of creating a new one each
        time. Matches on property_id + both participants being present.
        """
        a_convos = select(ConversationParticipant.conversation_id).where(
            ConversationParticipant.user_id == user_a
        )
        b_convos = select(ConversationParticipant.conversation_id).where(
            ConversationParticipant.user_id == user_b
        )
        result = await self.db.execute(
            select(Conversation)
            .where(
                Conversation.property_id == property_id,
                Conversation.id.in_(a_convos),
                Conversation.id.in_(b_convos),
            )
            .options(
                selectinload(Conversation.participants).selectinload(ConversationParticipant.user),
                selectinload(Conversation.listing),
            )
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self, user_id: uuid.UUID, page: int, per_page: int
    ) -> tuple[list[Conversation], int]:
        """
        Conversations this user participates in, most recently active
        first. Uses a subquery on messages to get the true last message
        time — conversations with new messages today sort above older
        conversations even if the conversation itself was created earlier.
        """
        # Subquery: conversation IDs where user is a participant
        participant_convos = select(ConversationParticipant.conversation_id).where(
            ConversationParticipant.user_id == user_id
        )

        # Subquery: latest message timestamp per conversation
        latest_msg = (
            select(
                Message.conversation_id,
                func.max(Message.created_at).label("last_message_at"),
            )
            .group_by(Message.conversation_id)
            .subquery()
        )

        total = (
            await self.db.execute(
                select(func.count()).select_from(
                    select(Conversation).where(Conversation.id.in_(participant_convos)).subquery()
                )
            )
        ).scalar_one()

        result = await self.db.execute(
            select(Conversation)
            .where(Conversation.id.in_(participant_convos))
            .outerjoin(latest_msg, Conversation.id == latest_msg.c.conversation_id)
            .options(
                selectinload(Conversation.listing),
                selectinload(Conversation.participants).selectinload(ConversationParticipant.user),
                selectinload(Conversation.messages),
            )
            .order_by(func.coalesce(latest_msg.c.last_message_at, Conversation.created_at).desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        return list(result.scalars().all()), total

    # ── Message reads (cursor-based) ──────────────────────────────────────────

    async def list_messages(
        self,
        conversation_id: uuid.UUID,
        cursor_created_at: str | None,
        limit: int,
    ) -> list[Message]:
        """
        Cursor pagination: cursor_created_at is an ISO timestamp string
        of the oldest message already loaded by the client. Returns
        messages strictly older than that, newest-first, up to `limit`.
        When cursor_created_at is None, returns the most recent `limit`
        messages (initial load).
        """
        q = select(Message).where(Message.conversation_id == conversation_id)
        if cursor_created_at:
            cursor_dt = datetime.fromisoformat(cursor_created_at)
            q = q.where(Message.created_at < cursor_dt)
        q = q.order_by(Message.created_at.desc()).limit(limit)
        result = await self.db.execute(q)
        return list(result.scalars().all())

    # ── Writes ────────────────────────────────────────────────────────────────

    async def create_conversation(
        self,
        property_id: uuid.UUID | None,
        participant_ids: list[uuid.UUID],
    ) -> Conversation:
        conversation = Conversation(property_id=property_id)
        self.db.add(conversation)
        await self.db.flush()

        for uid in participant_ids:
            self.db.add(ConversationParticipant(conversation_id=conversation.id, user_id=uid))
        await self.db.flush()
        return conversation

    async def create_message(
        self,
        conversation_id: uuid.UUID,
        sender_id: uuid.UUID,
        body: str,
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            sender_id=sender_id,
            body=body,
        )
        self.db.add(message)
        await self.db.flush()
        return message

    async def mark_read(self, participant: ConversationParticipant) -> ConversationParticipant:
        participant.mark_read()  # model method, sets last_read_at = now()
        await self.db.flush()
        return participant
