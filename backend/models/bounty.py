"""
models/bounty.py — ORM models for Skill Bounties.
"""
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BountyStatus(str, enum.Enum):
    open   = "open"
    closed = "closed"


class Bounty(Base):
    __tablename__ = "bounties"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    posted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # [{"skill": "Python", "min_score": 0.75}, ...]
    required_skills: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    reward_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[BountyStatus] = mapped_column(
        Enum(BountyStatus, name="bounty_status"), default=BountyStatus.open, nullable=False, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    poster: Mapped["User | None"] = relationship("User", foreign_keys=[posted_by], lazy="select")  # type: ignore
    claims: Mapped[list["BountyClaim"]] = relationship(
        "BountyClaim", back_populates="bounty", cascade="all, delete-orphan", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<Bounty id={self.id} title={self.title!r} status={self.status}>"


class BountyClaim(Base):
    __tablename__ = "bounty_claims"
    __table_args__ = (UniqueConstraint("bounty_id", "user_id", name="uq_bounty_claim"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bounty_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bounties.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    # Relationships
    bounty: Mapped["Bounty"] = relationship("Bounty", back_populates="claims")
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id], lazy="select")  # type: ignore

    def __repr__(self) -> str:
        return f"<BountyClaim bounty={self.bounty_id} user={self.user_id} score={self.score:.2f}>"