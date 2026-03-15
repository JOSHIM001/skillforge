"""
routers/roadmap.py — Skill Roadmap: 30/60/90 day personalized learning plan

POST /roadmap/generate   → generate a new roadmap based on skill matrix
GET  /roadmap            → get current roadmap
PUT  /roadmap/progress   → mark a task as complete/incomplete
DELETE /roadmap          → clear roadmap (regenerate)
"""

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import get_db
from middleware.auth_middleware import CurrentUser
from models.skill import SkillProfile
from redis_client import cache_get, cache_set, cache_delete
from services.ai_service import call_ai

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class GenerateRoadmapRequest(BaseModel):
    hours_per_day: float = Field(default=1.0, ge=0.5, le=8.0)
    target_role: str = Field(default="", max_length=100)
    focus_area: str = Field(default="balanced")  # balanced|frontend|backend|devops|ml


class ProgressUpdate(BaseModel):
    task_id: str
    completed: bool


# ── Redis keys ────────────────────────────────────────────────────────────────

def _roadmap_key(user_id: str) -> str:
    return f"roadmap:{user_id}"

def _progress_key(user_id: str) -> str:
    return f"roadmap_progress:{user_id}"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/generate", summary="Generate a personalized 30/60/90 day skill roadmap")
async def generate_roadmap(
    body: GenerateRoadmapRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict:

    # Get user's skill matrix
    result = await db.execute(
        select(SkillProfile).where(
            SkillProfile.user_id == current_user.id,
            SkillProfile.confidence > 0,
        )
    )
    profiles = result.scalars().all()
    skill_matrix = {p.skill_name: round(p.score, 2) for p in profiles}

    if not skill_matrix:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Complete at least 2 skill assessments before generating a roadmap"
        )

    system = (
        "You are a senior engineering career coach. Generate a detailed, actionable 30/60/90 day skill roadmap. "
        "Return ONLY valid JSON matching this exact schema: "
        '{"summary": "2 sentence overview of the plan", '
        '"target_level": "Junior|Mid|Senior|Staff", '
        '"estimated_salary_range": "string", '
        '"total_tasks": 24, '
        '"phases": ['
        '  {"phase": 1, "title": "30 Days", "goal": "string", "jobs_unlocked": 500, '
        '   "tasks": ['
        '     {"id": "t1", "title": "string", "description": "string", '
        '      "skill": "Python", "resource_type": "course|book|project|video|practice", '
        '      "resource_name": "string", "resource_url": "string", '
        '      "hours_needed": 5, "priority": "high|medium|low", '
        '      "why": "string"}'
        '   ]}'
        '], '
        '"quick_wins": [{"skill": "string", "action": "string", "impact": "string"}], '
        '"skills_to_drop": ["skill1"], '
        '"career_insight": "string"}'
    )

    hours_total_30 = body.hours_per_day * 30
    hours_total_60 = body.hours_per_day * 60
    hours_total_90 = body.hours_per_day * 90

    prompt = (
        f"Developer skill matrix: {skill_matrix}\n"
        f"Target role: {body.target_role or 'maximize overall marketability'}\n"
        f"Time available: {body.hours_per_day} hours/day "
        f"({hours_total_30:.0f}h in 30 days, {hours_total_60:.0f}h in 60 days, {hours_total_90:.0f}h in 90 days)\n"
        f"Focus: {body.focus_area}\n\n"
        "Generate a realistic 30/60/90 day roadmap that:\n"
        "1. Phase 1 (30 days): Fix the biggest weaknesses, quick wins\n"
        "2. Phase 2 (60 days): Build on strengths, fill market gaps\n"
        "3. Phase 3 (90 days): Advanced skills, specialization\n\n"
        "Each phase should have 6-8 specific tasks with:\n"
        "- Real resource names (actual courses, books, YouTube channels)\n"
        "- Realistic hours needed based on available time\n"
        "- Mix of: video courses, projects to build, books, practice problems\n"
        "- Why this task matters for their career\n\n"
        "Also identify:\n"
        "- 3 quick wins (skills close to threshold that need minimal work)\n"
        "- Skills that are less relevant in current market (to deprioritize)\n"
        "- A career insight specific to their skill profile\n\n"
        "Return ONLY valid JSON."
    )

    roadmap = await call_ai(prompt, system, cache_ttl=0)

    if not roadmap or "phases" not in roadmap:
        roadmap = _fallback_roadmap(skill_matrix, body.hours_per_day)

    # Add metadata
    roadmap["generated_at"] = datetime.now(timezone.utc).isoformat()
    roadmap["hours_per_day"] = body.hours_per_day
    roadmap["skill_snapshot"] = skill_matrix

    # Store in Redis (30 days TTL)
    await cache_set(_roadmap_key(str(current_user.id)), roadmap, ttl=86400 * 30)
    # Clear any old progress
    await cache_delete(_progress_key(str(current_user.id)))

    return roadmap


@router.get("", summary="Get current roadmap with progress")
async def get_roadmap(current_user: CurrentUser) -> dict:
    roadmap = await cache_get(_roadmap_key(str(current_user.id)))
    if not roadmap:
        return {"has_roadmap": False}

    # Get progress
    progress = await cache_get(_progress_key(str(current_user.id))) or {}

    # Calculate completion stats
    all_tasks = []
    for phase in roadmap.get("phases", []):
        for task in phase.get("tasks", []):
            task["completed"] = progress.get(task["id"], False)
            all_tasks.append(task)

    total = len(all_tasks)
    completed = sum(1 for t in all_tasks if t["completed"])
    completion_pct = round(completed / total * 100) if total > 0 else 0

    # Phase completion
    for phase in roadmap.get("phases", []):
        phase_tasks = phase.get("tasks", [])
        phase_done = sum(1 for t in phase_tasks if progress.get(t["id"], False))
        phase["completed_count"] = phase_done
        phase["total_count"] = len(phase_tasks)
        phase["completion_pct"] = round(phase_done / len(phase_tasks) * 100) if phase_tasks else 0

    roadmap["has_roadmap"] = True
    roadmap["overall_completion"] = completion_pct
    roadmap["completed_tasks"] = completed
    roadmap["total_tasks_count"] = total

    return roadmap


@router.put("/progress", summary="Mark a task as complete or incomplete")
async def update_progress(
    body: ProgressUpdate,
    current_user: CurrentUser,
) -> dict:
    roadmap = await cache_get(_roadmap_key(str(current_user.id)))
    if not roadmap:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No roadmap found")

    progress = await cache_get(_progress_key(str(current_user.id))) or {}
    progress[body.task_id] = body.completed
    await cache_set(_progress_key(str(current_user.id)), progress, ttl=86400 * 30)

    # Return updated stats
    all_tasks = []
    for phase in roadmap.get("phases", []):
        for task in phase.get("tasks", []):
            all_tasks.append(task["id"])

    total = len(all_tasks)
    completed = sum(1 for tid in all_tasks if progress.get(tid, False))

    return {
        "task_id": body.task_id,
        "completed": body.completed,
        "overall_completion": round(completed / total * 100) if total > 0 else 0,
        "completed_tasks": completed,
        "total_tasks": total,
    }


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, summary="Clear roadmap")
async def delete_roadmap(current_user: CurrentUser) -> None:
    await cache_delete(_roadmap_key(str(current_user.id)))
    await cache_delete(_progress_key(str(current_user.id)))


# ── Fallback ──────────────────────────────────────────────────────────────────

def _fallback_roadmap(skill_matrix: dict, hours_per_day: float) -> dict:
    weak = [(s, v) for s, v in skill_matrix.items() if v < 0.6]
    weak.sort(key=lambda x: x[1])
    strong = [(s, v) for s, v in skill_matrix.items() if v >= 0.6]

    return {
        "summary": f"Focus on improving {weak[0][0] if weak else 'core skills'} first, then build depth in your strongest areas.",
        "target_level": "Mid",
        "estimated_salary_range": "₹12L–₹25L",
        "total_tasks": 18,
        "phases": [
            {
                "phase": 1,
                "title": "30 Days — Foundation",
                "goal": "Fix weakest skills to unlock more opportunities",
                "jobs_unlocked": 300,
                "tasks": [
                    {
                        "id": f"t{i+1}",
                        "title": f"Improve {skill}",
                        "description": f"Study {skill} fundamentals and practice problems",
                        "skill": skill,
                        "resource_type": "course",
                        "resource_name": f"{skill} on freeCodeCamp",
                        "resource_url": "https://freecodecamp.org",
                        "hours_needed": int(hours_per_day * 10),
                        "priority": "high",
                        "why": f"Your {skill} score is {round(score*100)}% — improving to 70% unlocks significantly more jobs"
                    }
                    for i, (skill, score) in enumerate(weak[:3])
                ]
            },
        ],
        "quick_wins": [
            {"skill": s, "action": "Take assessment again after 1 week study", "impact": "Unlock mid-level positions"}
            for s, v in weak[:3] if v > 0.4
        ],
        "skills_to_drop": [],
        "career_insight": "Focus on depth over breadth — being excellent at 3-4 skills beats being average at 10."
    }