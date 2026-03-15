"""
routers/ghost_interview.py — Ghost Interview: AI-powered mock technical interviews

POST /ghost-interview/start        → start a new interview session
POST /ghost-interview/{id}/respond → send answer, get AI interviewer response
GET  /ghost-interview/{id}/debrief → get final hiring recommendation + feedback
GET  /ghost-interview/history      → list past interviews
"""

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from middleware.auth_middleware import CurrentUser
from redis_client import cache_get, cache_set
from services.ai_service import call_ai
from models.skill import SkillProfile
from sqlalchemy import select

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class StartInterviewRequest(BaseModel):
    persona_name: str = Field(..., min_length=2, max_length=100)
    persona_role: str = Field(..., min_length=2, max_length=100)   # e.g. "Senior SWE at Google"
    persona_style: str = Field(default="professional")             # professional|tough|friendly|skeptical
    interview_type: str = Field(default="mixed")                   # technical|behavioral|mixed
    target_role: str = Field(..., min_length=2, max_length=100)    # role they're applying for
    num_questions: int = Field(default=8, ge=3, le=15)
    focus_skills: list[str] = Field(default_factory=list)          # skills to focus on


class RespondRequest(BaseModel):
    answer: str = Field(..., min_length=10, max_length=5000)


class InterviewMessage(BaseModel):
    role: str       # "interviewer" | "candidate"
    content: str
    timestamp: str
    question_num: int | None = None


class InterviewState(BaseModel):
    id: str
    persona_name: str
    persona_role: str
    interview_type: str
    target_role: str
    num_questions: int
    questions_asked: int
    messages: list[dict]
    status: str       # "active" | "completed"
    started_at: str
    skill_matrix: dict


# ── Session helpers ───────────────────────────────────────────────────────────

def _session_key(session_id: str) -> str:
    return f"ghost:{session_id}"

def _history_key(user_id: str) -> str:
    return f"ghost:history:{user_id}"


async def _get_session(session_id: str) -> dict | None:
    return await cache_get(_session_key(session_id))


async def _save_session(session: dict) -> None:
    await cache_set(_session_key(session["id"]), session, ttl=86400)  # 24hr


# ── AI interviewer ────────────────────────────────────────────────────────────

async def _get_interviewer_response(
    session: dict,
    candidate_answer: str,
) -> dict:
    """
    Get the AI interviewer's next response.
    Returns: {message, next_question, is_followup, interview_done}
    """
    persona_name  = session["persona_name"]
    persona_role  = session["persona_role"]
    persona_style = session.get("persona_style", "professional")
    interview_type = session["interview_type"]
    target_role   = session["target_role"]
    questions_asked = session["questions_asked"]
    num_questions = session["num_questions"]
    skill_matrix  = session.get("skill_matrix", {})
    messages      = session.get("messages", [])

    # Build conversation history for context
    history_text = ""
    for msg in messages[-6:]:  # last 6 messages for context
        role_label = persona_name if msg["role"] == "interviewer" else "Candidate"
        history_text += f"{role_label}: {msg['content']}\n\n"

    style_instructions = {
        "professional": "Be professional and thorough. Acknowledge good points before probing deeper.",
        "tough": "Be direct and challenging. Push back on vague answers. Don't accept surface-level responses.",
        "friendly": "Be warm and encouraging. Help the candidate feel comfortable while still being rigorous.",
        "skeptical": "Be skeptical of claims. Ask for proof, examples, and specifics constantly.",
    }.get(persona_style, "Be professional and thorough.")

    is_last = questions_asked >= num_questions - 1
    is_followup_candidate = (
        candidate_answer and
        len(candidate_answer) < 100 and
        questions_asked < num_questions
    )

    system = (
        f"You are {persona_name}, a {persona_role}. "
        f"You are conducting a {interview_type} interview for a {target_role} position. "
        f"{style_instructions} "
        "You speak naturally, like a real interviewer — not like an AI. "
        "Never break character. Never say you're an AI. "
        "Return ONLY valid JSON: "
        '{"message": "your response as the interviewer", '
        '"next_question": "the next interview question or empty string if done", '
        '"is_followup": false, '
        '"interview_done": false, '
        '"internal_score": 7}'
        " internal_score is 1-10, your private assessment of this answer."
    )

    skill_context = ""
    if skill_matrix:
        top = sorted(skill_matrix.items(), key=lambda x: x[1], reverse=True)[:4]
        skill_context = f"Candidate's verified skills: {dict(top)}. "

    prompt = (
        f"Interview context:\n"
        f"- Interviewer: {persona_name} ({persona_role})\n"
        f"- Role being interviewed for: {target_role}\n"
        f"- Interview type: {interview_type}\n"
        f"- Question {questions_asked + 1} of {num_questions}\n"
        f"- {skill_context}\n\n"
        f"Recent conversation:\n{history_text}\n"
        f"Candidate just answered: {candidate_answer}\n\n"
    )

    if is_last:
        prompt += (
            "This was the LAST question. "
            "Wrap up the interview naturally. Thank the candidate. "
            "Set interview_done to true. next_question should be empty. "
            "Give a warm but honest closing statement."
        )
    elif is_followup_candidate:
        prompt += (
            "The answer was brief/vague. "
            "Ask a sharp follow-up to dig deeper. Set is_followup to true. "
            "Don't move to a new topic yet."
        )
    else:
        prompt += (
            f"React to their answer (1-2 sentences acknowledging it). "
            f"Then ask question {questions_asked + 1} of {num_questions}. "
            f"Make it {'technical' if interview_type == 'technical' else 'behavioral' if interview_type == 'behavioral' else 'technical or behavioral based on flow'}. "
            "Set interview_done to false."
        )

    result = await call_ai(prompt, system, cache_ttl=0)

    if not result:
        return {
            "message": "That's an interesting perspective. Let me think about that...",
            "next_question": "Can you walk me through a challenging technical problem you solved recently?",
            "is_followup": False,
            "interview_done": is_last,
            "internal_score": 5,
        }

    return result


async def _generate_opening(session: dict) -> str:
    """Generate the interviewer's opening message."""
    persona_name = session["persona_name"]
    persona_role = session["persona_role"]
    target_role  = session["target_role"]
    interview_type = session["interview_type"]
    num_questions = session["num_questions"]
    persona_style = session.get("persona_style", "professional")

    style_voice = {
        "professional": "formal and professional",
        "tough": "direct and no-nonsense",
        "friendly": "warm and welcoming",
        "skeptical": "measured and analytical",
    }.get(persona_style, "professional")

    system = (
        f"You are {persona_name}, a {persona_role}. "
        "Write a natural interview opening. Sound like a real person, not a bot. "
        "Return ONLY valid JSON: {\"opening\": \"your opening message\"}"
    )

    prompt = (
        f"You're about to interview a candidate for a {target_role} position. "
        f"This will be a {interview_type} interview with {num_questions} questions. "
        f"Your speaking style is {style_voice}. "
        "Write a brief, natural opening (2-3 sentences): introduce yourself, "
        "set expectations for the interview, and ask them to start by introducing themselves briefly."
    )

    result = await call_ai(prompt, system, cache_ttl=0)
    return result.get("opening", f"Hi, I'm {persona_name}. Thanks for joining me today. Let's get started — tell me a bit about yourself and what brings you to this {target_role} role.")


async def _generate_debrief(session: dict) -> dict:
    """Generate the final hiring recommendation and detailed feedback."""
    persona_name = session["persona_name"]
    persona_role = session["persona_role"]
    target_role  = session["target_role"]
    messages     = session.get("messages", [])

    # Extract internal scores from session
    scores = session.get("answer_scores", [])
    questions_asked = session.get("questions_asked", 0)
    num_questions = session.get("num_questions", 8)
    
    # If no answers at all, return incomplete feedback
    if not scores or questions_asked == 0:
        return {
            "verdict": "Incomplete",
            "overall_score": 0,
            "summary": "The interview was ended before any questions were answered.",
            "strengths": [],
            "weaknesses": ["Interview not completed"],
            "standout_moment": "No answers provided",
            "improvement_areas": ["Complete at least 3 questions for a meaningful assessment"],
            "hiring_recommendation": "Cannot make a hiring recommendation — interview was ended before any questions were answered. Please try again and answer at least 3 questions.",
        }
    
    avg_score = sum(scores) / len(scores)
    
    # Penalize incomplete interviews slightly
    completion_rate = questions_asked / max(num_questions, 1)
    if completion_rate < 0.5:
        avg_score = avg_score * 0.85  # 15% penalty for less than half completed

    # Build full transcript
    transcript = ""
    for msg in messages:
        role_label = persona_name if msg["role"] == "interviewer" else "Candidate"
        transcript += f"{role_label}: {msg['content']}\n\n"

    system = (
        f"You are {persona_name}, a {persona_role}. "
        "You just finished interviewing a candidate. Give an honest, detailed debrief. "
        "Return ONLY valid JSON: "
        '{"verdict": "Strong Hire|Hire|No Hire|Strong No Hire", '
        '"overall_score": 7.5, '
        '"summary": "2-3 sentence overall assessment", '
        '"strengths": ["strength1", "strength2", "strength3"], '
        '"weaknesses": ["weakness1", "weakness2"], '
        '"standout_moment": "the best thing they said or did", '
        '"improvement_areas": ["area1", "area2"], '
        '"hiring_recommendation": "detailed paragraph explaining your decision"}'
    )

    completion_pct = round(questions_asked / max(num_questions, 1) * 100)
    prompt = (
        f"You interviewed a candidate for {target_role}. "
        f"They completed {questions_asked} of {num_questions} questions ({completion_pct}% of the interview). "
        f"Their average answer quality was {avg_score:.1f}/10. "
        f"{'The interview was ended early.' if completion_pct < 100 else 'The interview was completed.'} "
        f"Full interview transcript:\n\n{transcript[:3000]}\n\n"
        "Give an honest, thorough debrief as the interviewer. "
        "Be specific — reference actual things they said. "
        f"{'Note that the interview was incomplete when scoring.' if completion_pct < 80 else ''} "
        "Return ONLY valid JSON."
    )

    result = await call_ai(prompt, system, cache_ttl=0)

    if not result:
        verdict = "Hire" if avg_score >= 6.5 else "No Hire"
        return {
            "verdict": verdict,
            "overall_score": round(avg_score, 1),
            "summary": "Interview completed. See detailed feedback below.",
            "strengths": ["Completed the interview"],
            "weaknesses": ["Could not generate detailed feedback"],
            "standout_moment": "N/A",
            "improvement_areas": ["Practice more"],
            "hiring_recommendation": f"Based on the interview performance, verdict is {verdict}.",
        }

    return result


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/start", summary="Start a new Ghost Interview session")
async def start_interview(
    body: StartInterviewRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    # Get user's skill matrix for context
    result = await db.execute(
        select(SkillProfile).where(
            SkillProfile.user_id == current_user.id,
            SkillProfile.confidence > 0,
        )
    )
    profiles = result.scalars().all()
    skill_matrix = {p.skill_name: round(p.score, 2) for p in profiles}

    # Create session
    session_id = str(uuid.uuid4())[:8].upper()
    session = {
        "id": session_id,
        "user_id": str(current_user.id),
        "persona_name": body.persona_name,
        "persona_role": body.persona_role,
        "persona_style": body.persona_style,
        "interview_type": body.interview_type,
        "target_role": body.target_role,
        "num_questions": body.num_questions,
        "questions_asked": 0,
        "messages": [],
        "answer_scores": [],
        "status": "active",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "skill_matrix": skill_matrix,
    }

    # Generate opening message
    opening = await _generate_opening(session)
    session["messages"].append({
        "role": "interviewer",
        "content": opening,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question_num": 0,
    })

    await _save_session(session)

    # Add to user's history
    history = await cache_get(_history_key(str(current_user.id))) or []
    history.insert(0, {
        "id": session_id,
        "persona_name": body.persona_name,
        "persona_role": body.persona_role,
        "target_role": body.target_role,
        "interview_type": body.interview_type,
        "started_at": session["started_at"],
        "status": "active",
        "verdict": None,
    })
    await cache_set(_history_key(str(current_user.id)), history[:20], ttl=86400 * 30)

    return {
        "session_id": session_id,
        "opening_message": opening,
        "persona_name": body.persona_name,
        "persona_role": body.persona_role,
        "num_questions": body.num_questions,
    }


@router.post("/{session_id}/respond", summary="Submit answer and get interviewer response")
async def respond(
    session_id: str,
    body: RespondRequest,
    current_user: CurrentUser,
) -> dict:
    session = await _get_session(session_id.upper())
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview session not found")
    if session["user_id"] != str(current_user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your interview")
    if session["status"] == "completed":
        raise HTTPException(status.HTTP_409_CONFLICT, "Interview already completed")

    # Handle early end signal
    if body.answer == "[INTERVIEW_ENDED_EARLY]":
        session["status"] = "completed"
        session["completed_at"] = datetime.now(timezone.utc).isoformat()
        await _save_session(session)
        return {
            "interviewer_message": "Interview ended early.",
            "is_followup": False,
            "interview_done": True,
            "questions_asked": session["questions_asked"],
            "questions_remaining": 0,
            "progress_pct": 100,
        }

    # Add candidate's answer to history
    session["messages"].append({
        "role": "candidate",
        "content": body.answer,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question_num": session["questions_asked"] + 1,
    })

    # Get interviewer response
    ai_response = await _get_interviewer_response(session, body.answer)

    # Track internal score
    internal_score = ai_response.get("internal_score", 5)
    session["answer_scores"].append(internal_score)

    # Increment questions if not a follow-up
    if not ai_response.get("is_followup", False):
        session["questions_asked"] += 1

    interview_done = ai_response.get("interview_done", False) or session["questions_asked"] >= session["num_questions"]

    # Build interviewer message
    interviewer_msg = ai_response.get("message", "")
    next_q = ai_response.get("next_question", "")
    full_message = interviewer_msg
    if next_q and not interview_done:
        full_message = interviewer_msg + ("\n\n" if interviewer_msg else "") + next_q

    session["messages"].append({
        "role": "interviewer",
        "content": full_message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "question_num": session["questions_asked"],
    })

    if interview_done:
        session["status"] = "completed"
        session["completed_at"] = datetime.now(timezone.utc).isoformat()

    await _save_session(session)

    return {
        "interviewer_message": full_message,
        "is_followup": ai_response.get("is_followup", False),
        "interview_done": interview_done,
        "questions_asked": session["questions_asked"],
        "questions_remaining": max(0, session["num_questions"] - session["questions_asked"]),
        "progress_pct": round(session["questions_asked"] / session["num_questions"] * 100),
    }


@router.get("/{session_id}/debrief", summary="Get final hiring recommendation and feedback")
async def get_debrief(
    session_id: str,
    current_user: CurrentUser,
) -> dict:
    session = await _get_session(session_id.upper())
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview session not found")
    if session["user_id"] != str(current_user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your interview")

    # Generate debrief if not cached
    debrief_key = f"ghost:debrief:{session_id}"
    cached_debrief = await cache_get(debrief_key)
    if cached_debrief:
        return cached_debrief

    debrief = await _generate_debrief(session)
    debrief["session_id"] = session_id
    debrief["persona_name"] = session["persona_name"]
    debrief["persona_role"] = session["persona_role"]
    debrief["target_role"] = session["target_role"]
    debrief["questions_asked"] = session["questions_asked"]
    debrief["duration_minutes"] = _calc_duration(session)

    await cache_set(debrief_key, debrief, ttl=86400 * 7)

    # Update history with verdict
    history = await cache_get(_history_key(str(current_user.id))) or []
    for item in history:
        if item["id"] == session_id:
            item["status"] = "completed"
            item["verdict"] = debrief.get("verdict")
            break
    await cache_set(_history_key(str(current_user.id)), history, ttl=86400 * 30)

    return debrief


@router.get("/history", summary="List past interview sessions")
async def get_history(current_user: CurrentUser) -> dict:
    history = await cache_get(_history_key(str(current_user.id))) or []
    return {"interviews": history, "total": len(history)}


@router.get("/{session_id}/transcript", summary="Get full interview transcript")
async def get_transcript(
    session_id: str,
    current_user: CurrentUser,
) -> dict:
    session = await _get_session(session_id.upper())
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    if session["user_id"] != str(current_user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Not your interview")

    return {
        "session_id": session_id,
        "messages": session["messages"],
        "status": session["status"],
        "questions_asked": session["questions_asked"],
    }


def _calc_duration(session: dict) -> int:
    try:
        start = datetime.fromisoformat(session["started_at"])
        end_str = session.get("completed_at", datetime.now(timezone.utc).isoformat())
        end = datetime.fromisoformat(end_str)
        return max(1, int((end - start).total_seconds() / 60))
    except Exception:
        return 0