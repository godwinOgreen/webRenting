# app/domains/messaging/models.py

"""
Domain: Messaging
Tables: conversations, conversation_participants, messages

Three models that together form the in-app messaging system.

Architecture:
  Conversation            — a thread (optionally linked to a property)
  ConversationParticipant   — junction table (who's in the thread + read tracking)
  Message                 — a single message within a thread

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
  that user.

Why conversations are never deleted:
  Conversations are permanent records for dispute resolution and auditing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
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
    A messaging thread between two or more users.
    """

    __tablename__ = "conversations"

    # ── Optional link to property ─────────────────────────────────────────────
    property_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment=(
            "The property this conversation is about. "
            "SET NULL: property deleted → conversation survives. "
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
    Composite PK (conversation_id, user_id).
    """

    __tablename__ = "conversation_participants"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        comment="CASCADE: conversation deleted → participant links are removed.",
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
        nullable=False,
        index=True,
        comment="RESTRICT: cannot delete user who is in active conversations.",
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
            "When the user last opened this conversation. "
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
        preview = self.body[:50] + "..." if len(self.body) > 50 else self.body
        return (
            f"<Message id={self.id} "
            f"conversation_id={self.conversation_id} "
            f"sender_id={self.sender_id} "
            f"body={preview!r}>"
        )