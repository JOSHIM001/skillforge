"""
models/skill.py — ORM models for skill assessment.

Tables:
  skill_profiles     — per-user per-skill score + confidence + difficulty
  assessment_sessions — a single adaptive interview session
  question_history   — individual Q&A records within a session
"""

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Session status enum ───────────────────────────────────────────────────────

class SessionStatus(str, enum.Enum):
    active    = "active"
    completed = "completed"
    abandoned = "abandoned"


# ── SkillProfile ──────────────────────────────────────────────────────────────

class SkillProfile(Base):
    __tablename__ = "skill_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", "skill_name", name="uq_skill_profile_user_skill"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    skill_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    # ── Scoring ───────────────────────────────────────────────
    # score: 0.0 (no knowledge) → 1.0 (expert). Starts at 0.5 (unknown).
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    # confidence: 0.0 (no data) → 1.0 (many questions answered)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # difficulty_level: 1–5, current adaptive question difficulty
    difficulty_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # ── Spaced repetition ─────────────────────────────────────
    last_assessed: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Timestamps ────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    # ── Relationships ─────────────────────────────────────────
    user: Mapped["User"] = relationship("User", back_populates="skill_profiles")  # type: ignore[name-defined]

    def __repr__(self) -> str:
        return f"<SkillProfile user={self.user_id} skill={self.skill_name!r} score={self.score:.2f}>"


# ── AssessmentSession ─────────────────────────────────────────────────────────

class AssessmentSession(Base):
    __tablename__ = "assessment_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    skill_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # ── Status ────────────────────────────────────────────────
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus, name="session_status"),
        nullable=False,
        default=SessionStatus.active,
        index=True,
    )
    questions_asked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    final_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ── Timestamps ────────────────────────────────────────────
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Relationships ─────────────────────────────────────────
    user: Mapped["User"] = relationship("User", back_populates="assessment_sessions")  # type: ignore[name-defined]
    questions: Mapped[list["QuestionHistory"]] = relationship(
        "QuestionHistory",
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<AssessmentSession id={self.id} skill={self.skill_name!r} status={self.status}>"


# ── QuestionHistory ───────────────────────────────────────────────────────────

class QuestionHistory(Base):
    __tablename__ = "question_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessment_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Content ───────────────────────────────────────────────
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False)
    user_answer: Mapped[str] = mapped_column(Text, nullable=False)
    ai_grade: Mapped[float] = mapped_column(Float, nullable=False)
    ai_feedback: Mapped[str] = mapped_column(Text, nullable=False)

    # ── Timestamp ─────────────────────────────────────────────
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # ── Relationships ─────────────────────────────────────────
    session: Mapped["AssessmentSession"] = relationship(
        "AssessmentSession", back_populates="questions"
    )

    def __repr__(self) -> str:
        return f"<QuestionHistory id={self.id} grade={self.ai_grade:.2f}>"