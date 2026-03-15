"""
models/roadmap.py — Skill Roadmap database models
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Float, Boolean, DateTime, ForeignKey, JSON, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from database import Base


class RoadmapPlan(Base):
    """Stores the AI-generated roadmap plan for a user."""
    __tablename__ = "roadmap_plans"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id      = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_data    = Column(JSON, nullable=False)          # Full AI-generated plan
    hours_per_day = Column(Float, nullable=False, default=1.0)
    target_role  = Column(String(100), nullable=True)
    focus_area   = Column(String(50), nullable=False, default="balanced")
    created_at   = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at   = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc),
                          onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    task_progress = relationship("RoadmapTaskProgress", back_populates="plan",
                                  cascade="all, delete-orphan")


class RoadmapTaskProgress(Base):
    """Tracks completion status of each task in the roadmap."""
    __tablename__ = "roadmap_task_progress"

    id           = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    plan_id      = Column(UUID(as_uuid=True), ForeignKey("roadmap_plans.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    user_id      = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    task_id      = Column(String(50), nullable=False)    # e.g. "t1", "t2"
    task_skill   = Column(String(100), nullable=False)   # e.g. "Python"
    completed    = Column(Boolean, nullable=False, default=False)
    verified     = Column(Boolean, nullable=False, default=False)  # passed mini-assessment
    verify_score = Column(Float, nullable=True)          # score from verification
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at   = Column(DateTime(timezone=True), nullable=False,
                          default=lambda: datetime.now(timezone.utc))

    # Relationships
    plan = relationship("RoadmapPlan", back_populates="task_progress")