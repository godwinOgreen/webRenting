# app/domains/messaging/models.py

"""
Domain: Messaging
Tables: conversations, conversation_participants, messages

Three models that together form the in-app messaging system.

Architecture:
  Conversation              — a thread (optionally linked to a property)
  ConversationParticipant   — junction table (who's in the thread + read tracking)
  Message                   — a single message within a thread

How conversations start:
  1. Renter views a property and clicks "Contact Agent"
  2. messaging_service creates a Conversation (property_id set)
  3. messaging_service adds two ConversationParticipant rows:
     - the renter (user_id=renter)
     - the property owner (user_id=agent)
  4. First Message is sent in the same transaction

Read tracking:
  ConversationParticipant.last_read_at tracks when a user last opened a
  conversation. Messages with created_at > last_read_at are "unread" for
  that user. This is per-conversation, not per-message (simpler, sufficient
  for a real estate platform).

  Unread count for a user (in repository):
    SELECT COUNT(*) FROM messages m
    JOIN conversation_participants cp
      ON cp.conversation_id = m.conversation_id
    WHERE cp.user_id = :user_id
      AND m.created_at > cp.last_read_at
      AND m.sender_id != :user_id   -- don't count own messages as unread

Why conversations are never deleted:
  Conversations are permanent records. They contain negotiation history,
  agreement details, and may be needed for dispute resolution. If a user
  is suspended, their conversations remain but they can't send new messages.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Integer, Text, func, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import UUIDMixin, CreatedAtMixin

if TYPE_CHECKING:
    from app.domains.properties.models import Property
    from app.domains.users.models import User


# ─── Conversation Model ──────────────────────────────────────────────────────

class Conversation(Base, UUIDMixin, CreatedAtMixin):
    """
    A messaging thread between two or more users.

    Table: conversations

    property_id is nullable:
      - Set when conversation starts from a property listing (most common)
      - Null for general support conversations or admin-initiated threads

    Uses CreatedAtMixin (not TimestampMixin):
      Conversations are never updated. No updated_at needed.
      created_at is when the thread was started.
    """

    __tablename__ = "conversations"

    # ── Optional link to property ─────────────────────────────────────────────
    property_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="SET NULL"),
        nullable=True,
        comment=(
            "The property this conversation is about. "
            "SET NULL: property deleted → conversation survives (messages are permanent). "
            "NULL for support conversations or admin-initiated threads."
        ),
    )

    # ── Relationships ─────────────────────────────────────────────────────────

    # The property (nullable — may be a support conversation)
    listing: Mapped[Optional[Property]] = relationship(
        "Property",
        back_populates="conversations",
    )

    # Who's in this thread
    participants: Mapped[list[ConversationParticipant]] = relationship(
        "ConversationParticipant",
        back_populates="conversation",
        cascade="all, delete-orphan",
    )

    # All messages in this thread, ordered chronologically
    messages: Mapped[list[Message]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    # ── repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<Conversation id={self.id} "
            f"property_id={self.property_id}>"
        )


# ─── ConversationParticipant Model ───────────────────────────────────────────

class ConversationParticipant(Base):
    """
    Junction table linking users to conversations they're participating in.

    Table: conversation_participants

    Composite PK (conversation_id, user_id):
      No surrogate UUID needed. A user can only be in a conversation once.
      The composite PK enforces this at the database level.

    Uses no mixin (not even UUIDMixin):
      Composite PK tables don't have a single UUID id column.
      joined_at is declared directly, not via CreatedAtMixin, because
      the mixin expects to be combined with UUIDMixin which this model
      doesn't use.

    last_read_at:
      Tracks when the user last opened this conversation.
      Messages created after this timestamp are "unread" for this user.
      NULL means the user has never opened the conversation (all messages unread).
    """

    __tablename__ = "conversation_participants"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        comment="CASCADE: conversation deleted → participant links are meaningless.",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
        nullable=False,
        index=True,
        comment=(
            "Indexed separately from PK for 'find all conversations for user X' queries. "
            "RESTRICT: cannot delete user while they're a participant in conversations."
        ),
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(),
        comment="When this user joined the conversation.",
    )
    last_read_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment=(
            "When the user last opened this conversation. "
            "NULL = never opened (all messages are unread). "
            "Updated by messaging_service.mark_as_read(). "
            "Used to calculate unread message count."
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
    A single message within a conversation.

    Table: messages

    Messages are permanent records. Even if a user is suspended,
    their messages remain visible to other participants (for context
    and dispute resolution). The user simply can't send new messages.

    body has a CHECK constraint limiting to 5000 characters (MAX_MESSAGE_LENGTH).
    This prevents abuse (someone pasting a novel into a message) at the
    database level, not just the API level.

    Uses CreatedAtMixin (not TimestampMixin):
      Messages are never edited. No updated_at needed.
      created_at is when the message was sent.
    """

    __tablename__ = "messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment=(
            "Which conversation this message belongs to. "
            "Indexed: every message list query filters by conversation_id. "
            "CASCADE: conversation deleted → its messages are deleted too."
        ),
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
        comment=(
            "Who sent this message. "
            "RESTRICT: cannot delete user who sent messages (preserves message integrity). "
            "Suspended users' messages remain visible — they just can't send new ones."
        ),
    )
    body: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="Message content. Max 5000 characters (enforced by chk_message_length).",
    )

    # ── Constraints ───────────────────────────────────────────────────────────
    __table_args__ = (
        CheckConstraint(
            "length(body) > 0 AND length(body) <= 5000",
            name="chk_message_length",
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
        # Truncate body to 50 chars for readable repr
        preview = self.body[:50] + "..." if len(self.body) > 50 else self.body
        return (
            f"<Message id={self.id} "
            f"conversation_id={self.conversation_id} "
            f"sender_id={self.sender_id} "
            f"body={preview!r}>"
        )
