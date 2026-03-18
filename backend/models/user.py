import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    github_id: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    hashed_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    refresh_tokens: Mapped[list["RefreshToken"]] = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan", lazy="select")
    skill_profiles: Mapped[list["SkillProfile"]] = relationship("SkillProfile", back_populates="user", cascade="all, delete-orphan", lazy="select")  # type: ignore[name-defined]
    assessment_sessions: Mapped[list["AssessmentSession"]] = relationship("AssessmentSession", back_populates="user", cascade="all, delete-orphan", lazy="select")  # type: ignore[name-defined]
    room_memberships: Mapped[list["RoomMember"]] = relationship("RoomMember", back_populates="user", cascade="all, delete-orphan", lazy="select")  # type: ignore[name-defined]

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r}>"


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="refresh_tokens")

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode()).hexdigest()

    @property
    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) > self.expires_at

    @property
    def is_valid(self) -> bool:
        return not self.revoked and not self.is_expired