# app/domains/messaging/models.py

"""
Domain: Messaging
Tables: conversations, conversation_participants, messages

Three models implementing the conversation-thread messaging system.

Architecture:
  Conversation             — one chat thread, optionally tied to a property 
                            (through the API, a conversation always has a property_id)
  ConversationParticipant  — composite PK (conversation_id, user_id),
                             tracks last_read_at for unread counts
  Message                  — individual messages within a conversation

Why this design instead of sender_id/receiver_id on each message:
  - One query loads an entire thread: WHERE conversation_id = X
  - Unread count = COUNT(messages) WHERE created_at > participant.last_read_at
  - Supports >2 participants in future (e.g. admin joins a dispute thread)
  - WebSocket clients subscribe to one conversation_id channel

Flow (handled in messaging_service.py):
  1. Renter clicks "Message Agent" on a property
  2. Service checks: does a Conversation exist for (property_id, renter, agent)?
     - No  → create Conversation + 2 ConversationParticipant rows
     - Yes → reuse existing Conversation
  3. Messages are inserted with conversation_id + sender_id
  4. On read, update the reader's ConversationParticipant.last_read_at
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import CreatedAtMixin, UUIDMixin

if TYPE_CHECKING:
    from app.domains.properties.models import Property
    from app.domains.users.models import User


# ─── Conversation Model ──────────────────────────────────────────────────────

class Conversation(Base, UUIDMixin, CreatedAtMixin):
    """
    A single chat thread. property_id is nullable — a conversation may not
    be about a specific property (e.g. a support thread).
    """

    __tablename__ = "conversations"

    property_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment=(
            "The property this conversation is about. "
            "SET NULL: property deleted → conversation survives."
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────

    listing: Mapped[Optional[Property]] = relationship(
        "Property",
        back_populates="conversations",
    )

    participants: Mapped[list[ConversationParticipant]] = relationship(
        "ConversationParticipant",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )

    messages: Mapped[list[Message]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    # ── Computed / Helper Methods ─────────────────────────────────────────────

    @property
    def last_message(self) -> Optional[Message]:
        """Most recent message in the thread, or None if empty."""
        return self.messages[-1] if self.messages else None

    def get_participant(self, user_id: uuid.UUID) -> Optional[ConversationParticipant]:
        """Find a specific participant's row to check/update last_read_at."""
        for p in self.participants:
            if p.user_id == user_id:
                return p
        return None

    def unread_count_for(self, user_id: uuid.UUID) -> int:
        """
        Number of messages this user hasn't read yet.
        
        NOTE: Iterates loaded in-memory messages. Use aggregate DB queries
        for bulk inbox listing to avoid N+1 queries.
        """
        participant = self.get_participant(user_id)
        last_read = participant.last_read_at if participant else None

        count = 0
        for msg in self.messages:
            if msg.sender_id == user_id:
                continue
            if last_read is None or msg.created_at > last_read:
                count += 1
        return count

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<Conversation id={self.id} "
            f"property_id={self.property_id} "
            f"participants={len(self.participants)} "
            f"messages={len(self.messages)}>"
        )


# ─── ConversationParticipant Model ───────────────────────────────────────────

class ConversationParticipant(Base):
    """
    Junction table linking users to conversations they participate in.
    Composite PK (conversation_id, user_id).
    """

    __tablename__ = "conversation_participants"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        comment="CASCADE: conversation deleted → participant links removed.",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
        nullable=False,
        index=True,
        comment="RESTRICT: cannot delete a user with active conversation history.",
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="When this user joined the conversation.",
    )
    last_read_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment=(
            "Updated when the user opens this conversation. "
            "NULL = never opened (all messages are unread)."
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────

    conversation: Mapped[Conversation] = relationship(
        "Conversation",
        back_populates="participants",
    )
    user: Mapped[User] = relationship(
        "User",
        back_populates="conversation_participants",
    )

    # ── Helper Methods ────────────────────────────────────────────────────────

    def mark_read(self) -> None:
        """Sets last_read_at to current UTC time in memory."""
        self.last_read_at = datetime.now(tz=timezone.utc)

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<ConversationParticipant "
            f"conversation_id={self.conversation_id} "
            f"user_id={self.user_id}>"
        )


# ─── Message Model ───────────────────────────────────────────────────────────

class Message(Base, UUIDMixin, CreatedAtMixin):
    """
    A single message within a conversation thread.
    """

    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Which conversation this message belongs to.",
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment="Who sent this message.",
    )
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Message content. Max 5000 characters.",
    )

    # ── Constraints & Indexes ─────────────────────────────────────────────────

    __table_args__ = (
        CheckConstraint(
            "length(trim(body)) > 0 AND length(body) <= 5000",
            name="chk_message_length",
        ),
        Index(
            "ix_messages_conversation_created_at",
            "conversation_id",
            "created_at",
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────

    conversation: Mapped[Conversation] = relationship(
        "Conversation",
        back_populates="messages",
    )
    sender: Mapped[User] = relationship(
        "User",
        back_populates="messages_sent",
    )

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        preview = self.body[:30] + "..." if len(self.body) > 30 else self.body
        return (
            f"<Message id={self.id} "
            f"conversation_id={self.conversation_id} "
            f"sender_id={self.sender_id} "
            f"body={preview!r}>"
        )