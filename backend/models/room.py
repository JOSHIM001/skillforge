"""
models/room.py — ORM models for collaboration rooms.

Tables:
  collab_rooms  — a named room with a short code (e.g. "ABC123")
  room_members  — join table: which users are in which room
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── CollabRoom ────────────────────────────────────────────────────────────────

class CollabRoom(Base):
    __tablename__ = "collab_rooms"

    # Short alphanumeric code — used as the join URL e.g. "/room/ABC123"
    id: Mapped[str] = mapped_column(String(8), primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Creator can be NULL if the user later deletes their account
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # ── Relationships ─────────────────────────────────────────
    members: Mapped[list["RoomMember"]] = relationship(
        "RoomMember",
        back_populates="room",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<CollabRoom id={self.id!r} name={self.name!r}>"


# ── RoomMember ────────────────────────────────────────────────────────────────

class RoomMember(Base):
    __tablename__ = "room_members"

    # Composite primary key: one row per (room, user) pair
    room_id: Mapped[str] = mapped_column(
        String(8),
        ForeignKey("collab_rooms.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # ── Relationships ─────────────────────────────────────────
    room: Mapped["CollabRoom"] = relationship("CollabRoom", back_populates="members")
    user: Mapped["User"] = relationship("User", back_populates="room_memberships")  # type: ignore[name-defined]

    def __repr__(self) -> str:
        return f"<RoomMember room={self.room_id!r} user={self.user_id}>"