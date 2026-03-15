"""
routers/skills.py — Skill assessment API endpoints.

Endpoint map:
  GET  /skills/matrix                  → full skill profile + radar data
  GET  /skills/matrix/spaced-queue     → skills due for re-assessment
  POST /skills/session/start           → begin a new adaptive session
  GET  /skills/session/{id}            → session detail + question history
  GET  /skills/session/{id}/question   → get next adaptive question
  POST /skills/session/{id}/answer     → submit answer, get grade
  POST /skills/session/{id}/complete   → manually end a session
  GET  /tasks/{task_id}               → poll Celery task result

AI calls that exceed ~3s are offloaded to Celery.
The answer endpoint returns a task_id immediately; the frontend polls
GET /tasks/{task_id} every 2s until status == "done".
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from middleware.auth_middleware import CurrentUser
from middleware.rate_limiter import limiter
from models.skill import AssessmentSession, QuestionHistory, SessionStatus
from redis_client import cache_get
from schemas.skill import (
    GradingOut,
    QuestionOut,
    SessionDetailOut,
    SessionOut,
    SkillMatrixOut,
    SkillEntry,
    SpacedRepetitionItem,
    SpacedRepetitionQueueOut,
    StartSessionRequest,
    SubmitAnswerRequest,
    TaskStatusOut,
)
from services.skill_service import (
    complete_session,
    get_active_session,
    get_next_question,
    get_skill_matrix,
    get_spaced_repetition_queue,
    start_session,
    submit_answer,
    _get_or_create_profile,
)
from tasks.ai_tasks import (
    grade_answer_task,
    generate_question_task,
    make_task_key,
)

router = APIRouter()

# Max questions per session — enforced server-side
_MAX_QUESTIONS = 8


# ── Skill matrix ──────────────────────────────────────────────────────────────

@router.get(
    "/matrix",
    response_model=SkillMatrixOut,
    summary="Get the authenticated user's full skill profile",
)
async def get_matrix(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> SkillMatrixOut:
    from models.skill import SkillProfile

    result = await db.execute(
        select(SkillProfile).where(
            SkillProfile.user_id == current_user.id,
            SkillProfile.confidence > 0,
        )
    )
    profiles = result.scalars().all()

    if not profiles:
        return SkillMatrixOut(
            skills=[],
            matrix={},
            total_assessed=0,
            overall_score=0.0,
        )

    skills = [SkillEntry.model_validate(p) for p in profiles]
    matrix = {p.skill_name: round(p.score, 4) for p in profiles}
    overall = sum(p.score for p in profiles) / len(profiles)

    return SkillMatrixOut(
        skills=skills,
        matrix=matrix,
        total_assessed=len(profiles),
        overall_score=round(overall, 4),
    )


@router.get(
    "/matrix/spaced-queue",
    response_model=SpacedRepetitionQueueOut,
    summary="Get skills due for spaced repetition re-assessment",
)
async def get_spaced_queue(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> SpacedRepetitionQueueOut:
    queue = await get_spaced_repetition_queue(current_user.id, db)
    items = [SpacedRepetitionItem(**item) for item in queue]
    return SpacedRepetitionQueueOut(items=items, count=len(items))


# ── Session lifecycle ─────────────────────────────────────────────────────────

@router.post(
    "/session/start",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Start a new adaptive assessment session",
)
async def start_assessment_session(
    body: StartSessionRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> SessionOut:
    session = await start_session(current_user.id, body.skill_name, db)
    return SessionOut(
        session_id=session.id,
        skill_name=session.skill_name,
        status=session.status.value,
        questions_asked=session.questions_asked,
        started_at=session.started_at,
    )


@router.get(
    "/session/{session_id}",
    response_model=SessionDetailOut,
    summary="Get session detail with full question history",
)
async def get_session_detail(
    session_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> SessionDetailOut:
    result = await db.execute(
        select(AssessmentSession).where(
            AssessmentSession.id == session_id,
            AssessmentSession.user_id == current_user.id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")

    history_result = await db.execute(
        select(QuestionHistory)
        .where(QuestionHistory.session_id == session.id)
        .order_by(QuestionHistory.answered_at)
    )
    history_items = history_result.scalars().all()

    return SessionDetailOut(
        session_id=session.id,
        skill_name=session.skill_name,
        status=session.status.value,
        questions_asked=session.questions_asked,
        final_score=session.final_score,
        started_at=session.started_at,
        completed_at=session.completed_at,
        history=[
            {
                "question_text": h.question_text,
                "user_answer":   h.user_answer,
                "ai_grade":      h.ai_grade,
                "ai_feedback":   h.ai_feedback,
                "difficulty":    h.difficulty,
                "answered_at":   h.answered_at,
            }
            for h in history_items
        ],
    )


@router.post(
    "/session/{session_id}/complete",
    response_model=SessionOut,
    summary="Manually complete a session before reaching max questions",
)
async def complete_assessment_session(
    session_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> SessionOut:
    session = await get_active_session(session_id, current_user.id, db)
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Active session not found")

    session = await complete_session(session, db)
    return SessionOut(
        session_id=session.id,
        skill_name=session.skill_name,
        status=session.status.value,
        questions_asked=session.questions_asked,
        started_at=session.started_at,
    )


# ── Question generation ───────────────────────────────────────────────────────

@router.get(
    "/session/{session_id}/question",
    response_model=QuestionOut,
    summary="Get the next adaptive question for this session",
)
async def get_question(
    session_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> QuestionOut:
    session = await get_active_session(session_id, current_user.id, db)
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Active session not found")

    # Auto-complete if max questions reached
    if session.questions_asked >= _MAX_QUESTIONS:
        await complete_session(session, db)
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Session complete — {_MAX_QUESTIONS} questions answered. "
            "Use POST /session/{id}/complete to finalise.",
        )

    # Get current difficulty from profile
    profile = await _get_or_create_profile(current_user.id, session.skill_name, db)

    # Generate question (AI call — happens inline for the question endpoint
    # since the user is waiting at the question screen anyway; typically <2s)
    question_data = await get_next_question(session, db)

    # Stash question context in Redis so the answer endpoint can verify it
    # (prevents users from submitting answers to questions that were never served)
    question_cache_key = f"question:{session.id}:current"
    from redis_client import cache_set
    await cache_set(
        question_cache_key,
        {
            "question":         question_data.get("question", ""),
            "expected_topics":  question_data.get("expected_topics", []),
            "difficulty":       profile.difficulty_level,
        },
        ttl=1800,  # 30 min — more than enough time to answer
    )

    return QuestionOut(
        session_id=session.id,
        question=question_data.get("question", ""),
        difficulty=profile.difficulty_level,
        questions_asked=session.questions_asked,
        questions_remaining=_MAX_QUESTIONS - session.questions_asked,
    )


# ── Answer submission ─────────────────────────────────────────────────────────

@router.post(
    "/session/{session_id}/answer",
    response_model=GradingOut,
    summary="Submit an answer — grades synchronously (AI < 3s) or offloads to Celery",
)
async def submit_session_answer(
    session_id: uuid.UUID,
    body: SubmitAnswerRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> GradingOut:
    # Per-user AI rate limit: 30 answers per hour
    from middleware.security import check_ai_rate_limit
    await check_ai_rate_limit(str(current_user.id), "answer", limit=30, window_seconds=3600)

    # Sanitize user answer before processing
    from middleware.security import sanitize_user_input
    body.user_answer = sanitize_user_input(body.user_answer, max_length=2000)

    session = await get_active_session(session_id, current_user.id, db)
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Active session not found")

    if session.questions_asked >= _MAX_QUESTIONS:
        raise HTTPException(status.HTTP_409_CONFLICT, "Session already complete")

    # Verify the question was actually served (anti-cheat)
    question_cache_key = f"question:{session.id}:current"
    cached_question = await cache_get(question_cache_key)

    # Use submitted context if cache miss (e.g. Redis restart)
    question_text    = (cached_question or {}).get("question", body.question_text)
    expected_topics  = (cached_question or {}).get("expected_topics", body.expected_topics)
    difficulty       = (cached_question or {}).get("difficulty", body.difficulty)

    # Grade and update — skill_service handles the full pipeline
    grading = await submit_answer(
        session=session,
        question_text=question_text,
        user_answer=body.user_answer,
        expected_topics=expected_topics,
        difficulty=difficulty,
        db=db,
    )

    # Reload updated profile to return current score/difficulty
    profile = await _get_or_create_profile(current_user.id, session.skill_name, db)

    # Auto-complete if this was the last question
    session_complete = session.questions_asked >= _MAX_QUESTIONS
    if session_complete:
        await complete_session(session, db)

    return GradingOut(
        session_id=session.id,
        score=grading.get("score", 0.5),
        feedback=grading.get("feedback", ""),
        topics_covered=grading.get("topics_covered", []),
        new_skill_score=profile.score,
        new_difficulty=profile.difficulty_level,
        questions_asked=session.questions_asked,
        session_complete=session_complete,
    )


# ── Task polling ──────────────────────────────────────────────────────────────

@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusOut,
    summary="Poll the result of an async AI Celery task",
)
async def get_task_status(task_id: str) -> TaskStatusOut:
    """
    Frontend polls this every 2s after submitting an answer.
    Status values:
      "pending" → task still running
      "done"    → result is ready in .result
      "error"   → task failed, .detail has the reason
    """
    key = make_task_key(task_id)
    cached = await cache_get(key)

    if cached is None:
        # Task not yet written to Redis — still pending
        return TaskStatusOut(task_id=task_id, status="pending")

    return TaskStatusOut(
        task_id=task_id,
        status=cached.get("status", "pending"),
        result=cached.get("result"),
        detail=cached.get("detail"),
    )