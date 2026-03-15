"""
schemas/skill.py — Request and response schemas for the /skills/* endpoints.

Every dict that crosses the API boundary is typed here.
No raw dicts in router return values — ever.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Session ───────────────────────────────────────────────────────────────────

class StartSessionRequest(BaseModel):
    skill_name: str = Field(..., min_length=1, max_length=100)

    @field_validator("skill_name")
    @classmethod
    def strip_and_title(cls, v: str) -> str:
        """Normalise e.g. ' python ' → 'Python'"""
        return v.strip()


class SessionOut(BaseModel):
    session_id: uuid.UUID
    skill_name: str
    status: str
    questions_asked: int
    started_at: datetime

    model_config = {"from_attributes": True}


# ── Question ──────────────────────────────────────────────────────────────────

class QuestionOut(BaseModel):
    """Returned when the frontend requests the next question."""
    session_id: uuid.UUID
    question: str
    difficulty: int = Field(..., ge=1, le=5)
    # Expected topics are NOT sent to the client — they're used server-side
    # for grading only. Sending them would let users cheat.
    questions_asked: int
    questions_remaining: int


# ── Answer ────────────────────────────────────────────────────────────────────

class SubmitAnswerRequest(BaseModel):
    question_text: str = Field(..., min_length=1)
    expected_topics: list[str] = Field(default_factory=list)
    user_answer: str = Field(..., min_length=1, max_length=8000)
    difficulty: int = Field(..., ge=1, le=5)

    @field_validator("user_answer")
    @classmethod
    def reject_placeholder_answers(cls, v: str) -> str:
        stripped = v.strip()
        if len(stripped) < 10:
            raise ValueError("Answer is too short to grade meaningfully")
        return stripped


class GradingOut(BaseModel):
    """Returned after an answer is graded."""
    session_id: uuid.UUID
    score: float = Field(..., ge=0.0, le=1.0)
    feedback: str
    topics_covered: list[str]
    new_skill_score: float = Field(..., ge=0.0, le=1.0)
    new_difficulty: int = Field(..., ge=1, le=5)
    questions_asked: int
    session_complete: bool


# ── Skill matrix ──────────────────────────────────────────────────────────────

class SkillEntry(BaseModel):
    skill_name: str
    score: float = Field(..., ge=0.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    difficulty_level: int = Field(..., ge=1, le=5)
    last_assessed: datetime | None

    model_config = {"from_attributes": True}


class SkillMatrixOut(BaseModel):
    """Full skill profile for the authenticated user."""
    skills: list[SkillEntry]
    # Flat dict for Chart.js radar chart: {"Python": 0.87, ...}
    matrix: dict[str, float]
    total_assessed: int
    overall_score: float    # weighted average across all skills


# ── Spaced repetition ─────────────────────────────────────────────────────────

class SpacedRepetitionItem(BaseModel):
    skill_name: str
    current_score: float
    confidence: float
    last_assessed: datetime | None
    due_label: str


class SpacedRepetitionQueueOut(BaseModel):
    items: list[SpacedRepetitionItem]
    count: int


# ── Task polling (for async Celery tasks) ─────────────────────────────────────

class TaskStatusOut(BaseModel):
    task_id: str
    status: str     # "pending" | "done" | "error"
    result: dict | None = None
    detail: str | None = None


# ── Session history ───────────────────────────────────────────────────────────

class QuestionHistoryItem(BaseModel):
    question_text: str
    user_answer: str
    ai_grade: float
    ai_feedback: str
    difficulty: int
    answered_at: datetime

    model_config = {"from_attributes": True}


class SessionDetailOut(BaseModel):
    session_id: uuid.UUID
    skill_name: str
    status: str
    questions_asked: int
    final_score: float | None
    started_at: datetime
    completed_at: datetime | None
    history: list[QuestionHistoryItem]