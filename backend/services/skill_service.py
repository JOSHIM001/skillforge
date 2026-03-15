"""
skill_service.py — Adaptive assessment algorithm and skill matrix management.

The scoring model:
  - Each question has a difficulty weight (1–5 scaled to 0.1–0.5)
  - Grade (0.0–1.0) shifts the score up or down from 0.5 center
  - Confidence grows with questions answered, capped at 1.0
  - Difficulty level adapts after every answer:
      grade >= 0.7 → difficulty up
      grade < 0.4  → difficulty down
      else         → hold
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.skill import AssessmentSession, QuestionHistory, SessionStatus, SkillProfile
from services.ai_service import generate_question, grade_answer


# ── Scoring constants ─────────────────────────────────────────────────────────
_DIFFICULTY_WEIGHT = {1: 0.10, 2: 0.15, 3: 0.20, 4: 0.35, 5: 0.50}
_CONFIDENCE_GAIN   = {1: 0.08, 2: 0.10, 3: 0.12, 4: 0.15, 5: 0.18}
_SPACED_REPETITION_DAYS = 30   # re-assess after this many days
_MIN_CONFIDENCE_FOR_REVIEW = 0.3   # skip skills we barely know


# ── Score update ──────────────────────────────────────────────────────────────
def compute_score_update(
    current_score: float,
    current_confidence: float,
    grade: float,
    difficulty: int,
) -> tuple[float, float, int]:
    """
    Returns (new_score, new_confidence, new_difficulty).

    Score shifts relative to the 0.5 midpoint, weighted by difficulty.
    A grade of exactly 0.5 leaves the score unchanged — only clearly
    right (>0.7) or wrong (<0.4) answers move it significantly.
    """
    weight = _DIFFICULTY_WEIGHT[difficulty]

    # Score delta: positive for good answers, negative for bad ones
    delta = (grade - 0.5) * weight
    new_score = max(0.0, min(1.0, current_score + delta))

    # Confidence grows with every answer; harder questions build more confidence
    confidence_delta = _CONFIDENCE_GAIN[difficulty]
    new_confidence = min(1.0, current_confidence + confidence_delta)

    # Adaptive difficulty
    if grade >= 0.7:
        new_difficulty = min(5, difficulty + 1)
    elif grade < 0.4:
        new_difficulty = max(1, difficulty - 1)
    else:
        new_difficulty = difficulty

    return new_score, new_confidence, new_difficulty


# ── Session management ────────────────────────────────────────────────────────
async def start_session(
    user_id: uuid.UUID,
    skill_name: str,
    db: AsyncSession,
) -> AssessmentSession:
    """
    Create a new AssessmentSession and return it.
    Marks any existing active sessions for this (user, skill) as abandoned.
    """
    # Abandon stale active sessions for this skill
    result = await db.execute(
        select(AssessmentSession).where(
            AssessmentSession.user_id == user_id,
            AssessmentSession.skill_name == skill_name,
            AssessmentSession.status == SessionStatus.active,
        )
    )
    for stale in result.scalars().all():
        stale.status = SessionStatus.abandoned
        stale.completed_at = datetime.now(timezone.utc)

    session = AssessmentSession(
        user_id=user_id,
        skill_name=skill_name,
        status=SessionStatus.active,
    )
    db.add(session)
    await db.flush()
    return session


async def get_active_session(
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> AssessmentSession | None:
    result = await db.execute(
        select(AssessmentSession).where(
            AssessmentSession.id == session_id,
            AssessmentSession.user_id == user_id,
            AssessmentSession.status == SessionStatus.active,
        )
    )
    return result.scalar_one_or_none()


# ── Question generation ───────────────────────────────────────────────────────
async def get_next_question(
    session: AssessmentSession,
    db: AsyncSession,
) -> dict:
    """
    Determine current difficulty from skill profile, generate a question
    avoiding repeats from this session's history.
    """
    # Get the user's current difficulty level for this skill
    profile = await _get_or_create_profile(session.user_id, session.skill_name, db)
    difficulty = profile.difficulty_level

    # Load previous questions in this session to avoid repetition
    history_result = await db.execute(
        select(QuestionHistory.question_text).where(
            QuestionHistory.session_id == session.id
        )
    )
    previous_questions = list(history_result.scalars().all())

    return await generate_question(
        skill=session.skill_name,
        difficulty=difficulty,
        previous_questions=previous_questions,
    )


# ── Answer submission ─────────────────────────────────────────────────────────
async def submit_answer(
    session: AssessmentSession,
    question_text: str,
    user_answer: str,
    expected_topics: list[str],
    difficulty: int,
    db: AsyncSession,
) -> dict:
    """
    Grade the answer, persist the result, update skill profile.
    Returns the grading dict from the AI.
    """
    # Grade via AI
    grading = await grade_answer(
        question=question_text,
        user_answer=user_answer,
        expected_topics=expected_topics,
        skill=session.skill_name,
    )
    score = grading.get("score", 0.5)

    # Persist question history
    history_entry = QuestionHistory(
        session_id=session.id,
        question_text=question_text,
        difficulty=difficulty,
        user_answer=user_answer,
        ai_grade=score,
        ai_feedback=grading.get("feedback", ""),
    )
    db.add(history_entry)

    # Update session counter
    session.questions_asked += 1

    # Update skill profile
    profile = await _get_or_create_profile(session.user_id, session.skill_name, db)
    new_score, new_confidence, new_difficulty = compute_score_update(
        current_score=profile.score,
        current_confidence=profile.confidence,
        grade=score,
        difficulty=profile.difficulty_level,
    )
    profile.score = new_score
    profile.confidence = new_confidence
    profile.difficulty_level = new_difficulty
    profile.last_assessed = datetime.now(timezone.utc)

    await db.flush()
    return grading


async def complete_session(
    session: AssessmentSession,
    db: AsyncSession,
) -> AssessmentSession:
    """Mark a session as completed and calculate its final score."""
    # Average grade across all questions in session
    history_result = await db.execute(
        select(QuestionHistory).where(QuestionHistory.session_id == session.id)
    )
    history = history_result.scalars().all()

    if history:
        session.final_score = sum(q.ai_grade for q in history) / len(history)
    else:
        session.final_score = 0.0

    session.status = SessionStatus.completed
    session.completed_at = datetime.now(timezone.utc)
    await db.flush()
    return session


# ── Skill matrix ──────────────────────────────────────────────────────────────
async def get_skill_matrix(
    user_id: uuid.UUID,
    db: AsyncSession,
) -> dict[str, float]:
    """
    Return the full skill matrix as {skill_name: score}.
    Only includes skills with confidence > 0 (at least one question answered).
    """
    result = await db.execute(
        select(SkillProfile).where(
            SkillProfile.user_id == user_id,
            SkillProfile.confidence > 0,
        )
    )
    profiles = result.scalars().all()
    return {p.skill_name: round(p.score, 4) for p in profiles}


# ── Spaced repetition queue ───────────────────────────────────────────────────
async def get_spaced_repetition_queue(
    user_id: uuid.UUID,
    db: AsyncSession,
) -> list[dict]:
    """
    Return skills due for re-assessment.
    Criteria:
      - Last assessed > 30 days ago (or never)
      - Confidence > 0.3 (enough data to be worth re-testing)
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=_SPACED_REPETITION_DAYS)

    result = await db.execute(
        select(SkillProfile).where(
            SkillProfile.user_id == user_id,
            SkillProfile.confidence >= _MIN_CONFIDENCE_FOR_REVIEW,
            (SkillProfile.last_assessed < cutoff) | (SkillProfile.last_assessed == None),  # noqa: E711
        )
    )
    profiles = result.scalars().all()

    queue = []
    for p in profiles:
        if p.last_assessed:
            days_ago = (datetime.now(timezone.utc) - p.last_assessed).days
            due_label = f"Due {days_ago} days overdue" if days_ago > _SPACED_REPETITION_DAYS else "Due today"
        else:
            due_label = "Never assessed"

        queue.append({
            "skill_name": p.skill_name,
            "current_score": round(p.score, 4),
            "confidence": round(p.confidence, 4),
            "last_assessed": p.last_assessed.isoformat() if p.last_assessed else None,
            "due_label": due_label,
        })

    # Prioritise: longest overdue first, then by score (reassess weakest)
    queue.sort(key=lambda x: (x["last_assessed"] or "", x["current_score"]))
    return queue


# ── Profile helper ────────────────────────────────────────────────────────────
async def _get_or_create_profile(
    user_id: uuid.UUID,
    skill_name: str,
    db: AsyncSession,
) -> SkillProfile:
    """
    Fetch a SkillProfile, creating it with defaults if it doesn't exist yet.
    Uses INSERT ... ON CONFLICT logic via SQLAlchemy to avoid race conditions.
    """
    result = await db.execute(
        select(SkillProfile).where(
            SkillProfile.user_id == user_id,
            SkillProfile.skill_name == skill_name,
        )
    )
    profile = result.scalar_one_or_none()

    if profile is None:
        profile = SkillProfile(
            user_id=user_id,
            skill_name=skill_name,
            score=0.5,           # start at midpoint, not 0
            confidence=0.0,
            difficulty_level=1,
        )
        db.add(profile)
        await db.flush()

    return profile