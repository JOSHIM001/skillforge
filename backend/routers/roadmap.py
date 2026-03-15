"""
routers/roadmap.py — Skill Roadmap with skill-verified task completion

POST /roadmap/generate                → generate AI roadmap, save to PostgreSQL
GET  /roadmap                         → get current roadmap with progress
GET  /roadmap/task/{task_id}/questions → get 5 verification questions for a task
POST /roadmap/task/{task_id}/verify   → submit answers, grade, mark task complete
PUT  /roadmap/task/{task_id}/complete → manually mark complete (no verification)
DELETE /roadmap                       → delete roadmap
"""

import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete

from database import get_db
from middleware.auth_middleware import CurrentUser
from models.skill import SkillProfile
from models.roadmap import RoadmapPlan, RoadmapTaskProgress
from services.ai_service import call_ai

router = APIRouter()


class GenerateRoadmapRequest(BaseModel):
    hours_per_day: float = Field(default=1.0, ge=0.5, le=8.0)
    target_role: str = Field(default="", max_length=100)
    focus_area: str = Field(default="balanced")


class VerifyAnswersRequest(BaseModel):
    answers: list[str]
    questions: list[str]


async def _get_skill_matrix(user_id: uuid.UUID, db: AsyncSession) -> dict:
    result = await db.execute(
        select(SkillProfile).where(SkillProfile.user_id == user_id, SkillProfile.confidence > 0)
    )
    return {p.skill_name: round(p.score, 2) for p in result.scalars().all()}


async def _get_active_plan(user_id: uuid.UUID, db: AsyncSession):
    result = await db.execute(
        select(RoadmapPlan).where(RoadmapPlan.user_id == user_id)
        .order_by(RoadmapPlan.created_at.desc()).limit(1)
    )
    return result.scalar_one_or_none()


async def _get_progress_map(plan_id: uuid.UUID, db: AsyncSession) -> dict:
    result = await db.execute(
        select(RoadmapTaskProgress).where(RoadmapTaskProgress.plan_id == plan_id)
    )
    return {p.task_id: p for p in result.scalars().all()}


def _enrich(plan_data: dict, progress_map: dict) -> dict:
    all_tasks = completed_tasks = 0
    for phase in plan_data.get("phases", []):
        phase_done = 0
        phase_total = len(phase.get("tasks", []))
        for task in phase.get("tasks", []):
            prog = progress_map.get(task["id"])
            task["completed"] = prog.completed if prog else False
            task["verified"] = prog.verified if prog else False
            task["verify_score"] = prog.verify_score if prog else None
            all_tasks += 1
            if task["completed"]:
                completed_tasks += 1
                phase_done += 1
        phase["completed_count"] = phase_done
        phase["total_count"] = phase_total
        phase["completion_pct"] = round(phase_done / phase_total * 100) if phase_total else 0
    plan_data["overall_completion"] = round(completed_tasks / all_tasks * 100) if all_tasks else 0
    plan_data["completed_tasks"] = completed_tasks
    plan_data["total_tasks_count"] = all_tasks
    plan_data["has_roadmap"] = True
    return plan_data


@router.post("/generate")
async def generate_roadmap(body: GenerateRoadmapRequest, current_user: CurrentUser, db: AsyncSession = Depends(get_db)) -> dict:
    skill_matrix = await _get_skill_matrix(current_user.id, db)
    if len(skill_matrix) < 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Complete at least 2 skill assessments first")

    system = (
        "You are a senior engineering career coach. Generate a skill roadmap. "
        "Return ONLY valid JSON: "
        '{"summary": "2 sentences", "target_level": "Junior|Mid|Senior|Staff", '
        '"estimated_salary_range": "₹12L-₹25L", '
        '"phases": [{"phase": 1, "title": "Phase 1 — Quick Wins", "goal": "string", "jobs_unlocked": 500, '
        '"tasks": [{"id": "t1", "title": "string", "description": "string", "skill": "Python", '
        '"resource_type": "course|book|project|video|practice", "resource_name": "string", '
        '"resource_url": "https://...", "hours_needed": 5, "priority": "high|medium|low", "why": "string"}]}], '
        '"quick_wins": [{"skill": "string", "action": "string", "impact": "string"}], '
        '"career_insight": "string"}'
    )

    prompt = (
        f"Skill matrix: {skill_matrix}\n"
        f"Target: {body.target_role or 'maximize marketability'}\n"
        f"Pace: {body.hours_per_day}h/day\nFocus: {body.focus_area}\n\n"
        "3 phases, 5-7 tasks each. Skill-gated not time-gated.\n"
        "Phase 1: Fix weaknesses + quick wins\n"
        "Phase 2: Deepen market strengths\n"
        "Phase 3: Advanced + interview prep\n"
        "Use real resources with real URLs. Salary in Indian Rupees (Lakhs).\n"
        "Return ONLY valid JSON."
    )

    plan_data = await call_ai(prompt, system, cache_ttl=0)
    if not plan_data or "phases" not in plan_data:
        plan_data = _fallback_roadmap(skill_matrix, body.hours_per_day)

    plan_data["generated_at"] = datetime.now(timezone.utc).isoformat()
    plan_data["hours_per_day"] = body.hours_per_day
    plan_data["skill_snapshot"] = skill_matrix

    await db.execute(delete(RoadmapPlan).where(RoadmapPlan.user_id == current_user.id))
    plan = RoadmapPlan(user_id=current_user.id, plan_data=plan_data,
                       hours_per_day=body.hours_per_day, target_role=body.target_role, focus_area=body.focus_area)
    db.add(plan)
    await db.flush()

    plan_data["has_roadmap"] = True
    plan_data["overall_completion"] = 0
    plan_data["completed_tasks"] = 0
    plan_data["total_tasks_count"] = sum(len(p.get("tasks", [])) for p in plan_data.get("phases", []))
    for phase in plan_data.get("phases", []):
        phase["completed_count"] = 0
        phase["total_count"] = len(phase.get("tasks", []))
        phase["completion_pct"] = 0
        for task in phase.get("tasks", []):
            task["completed"] = False
            task["verified"] = False
            task["verify_score"] = None
    return plan_data


@router.get("")
async def get_roadmap(current_user: CurrentUser, db: AsyncSession = Depends(get_db)) -> dict:
    plan = await _get_active_plan(current_user.id, db)
    if not plan:
        return {"has_roadmap": False}
    progress_map = await _get_progress_map(plan.id, db)
    return _enrich(dict(plan.plan_data), progress_map)


@router.get("/task/{task_id}/questions")
async def get_task_questions(task_id: str, current_user: CurrentUser, db: AsyncSession = Depends(get_db)) -> dict:
    plan = await _get_active_plan(current_user.id, db)
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No roadmap found")

    task = next((t for phase in plan.plan_data.get("phases", []) for t in phase.get("tasks", []) if t["id"] == task_id), None)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")

    system = "Generate 5 verification questions. Return ONLY valid JSON: {\"questions\": [\"q1\",\"q2\",\"q3\",\"q4\",\"q5\"]}"
    prompt = (
        f"Task: {task['title']}\nSkill: {task.get('skill')}\n"
        f"Resource: {task.get('resource_name')}\nWhat they learned: {task.get('description')}\n\n"
        "5 practical questions that verify they actually learned this. Progressive difficulty. Return ONLY valid JSON."
    )

    result = await call_ai(prompt, system, cache_ttl=1800)
    questions = result.get("questions", []) if result else []
    if len(questions) < 5:
        s = task.get("skill", "this topic")
        questions = [
            f"Explain the core concept you learned about {s}",
            f"Give a practical example of using {s}",
            f"What are the main advantages and trade-offs of {s}?",
            f"Describe a real problem you could solve with {s}",
            f"What would you do next to deepen your {s} knowledge?",
        ]

    return {"task_id": task_id, "task_title": task["title"], "skill": task.get("skill"), "questions": questions[:5]}


@router.post("/task/{task_id}/verify")
async def verify_task(task_id: str, body: VerifyAnswersRequest, current_user: CurrentUser, db: AsyncSession = Depends(get_db)) -> dict:
    plan = await _get_active_plan(current_user.id, db)
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No roadmap found")

    task = next((t for phase in plan.plan_data.get("phases", []) for t in phase.get("tasks", []) if t["id"] == task_id), None)
    if not task:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Task not found")

    skill = task.get("skill", "Programming")
    qa_pairs = "\n".join([f"Q{i+1}: {q}\nA{i+1}: {a}" for i, (q, a) in enumerate(zip(body.questions, body.answers))])

    system = (
        "Grade these verification answers. Return ONLY valid JSON: "
        '{"overall_score": 0.75, "passed": true, '
        '"feedback": ["fb1","fb2","fb3","fb4","fb5"], "summary": "2 sentences"}'
    )
    prompt = (
        f"Task: {task['title']}\nSkill: {skill}\n\n{qa_pairs}\n\n"
        "Grade 0-1. Pass = 60%+. Be fair but rigorous. Return ONLY valid JSON."
    )

    result = await call_ai(prompt, system, cache_ttl=0)
    if not result:
        result = {"overall_score": 0.5, "passed": False, "feedback": ["Grading failed"]*5, "summary": "Could not grade"}

    score = result.get("overall_score", 0)
    passed = score >= 0.60

    progress_map = await _get_progress_map(plan.id, db)
    existing = progress_map.get(task_id)
    if existing:
        existing.completed = passed
        existing.verified = True
        existing.verify_score = score
        if passed:
            existing.completed_at = datetime.now(timezone.utc)
    else:
        db.add(RoadmapTaskProgress(
            plan_id=plan.id, user_id=current_user.id, task_id=task_id,
            task_skill=skill, completed=passed, verified=True, verify_score=score,
            completed_at=datetime.now(timezone.utc) if passed else None,
        ))
    await db.flush()

    return {
        "task_id": task_id, "passed": passed, "score": round(score * 100),
        "feedback": result.get("feedback", []), "summary": result.get("summary", ""),
        "message": "Task verified and complete!" if passed else f"Score {round(score*100)}% — need 60% to pass. Review and try again.",
    }


@router.put("/task/{task_id}/complete")
async def complete_task(task_id: str, current_user: CurrentUser, db: AsyncSession = Depends(get_db)) -> dict:
    plan = await _get_active_plan(current_user.id, db)
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No roadmap found")

    task_skill = next((t.get("skill","") for phase in plan.plan_data.get("phases",[]) for t in phase.get("tasks",[]) if t["id"]==task_id), "Unknown")
    progress_map = await _get_progress_map(plan.id, db)
    existing = progress_map.get(task_id)

    if existing:
        existing.completed = True
        existing.completed_at = datetime.now(timezone.utc)
    else:
        db.add(RoadmapTaskProgress(plan_id=plan.id, user_id=current_user.id, task_id=task_id,
                                    task_skill=task_skill, completed=True, verified=False,
                                    completed_at=datetime.now(timezone.utc)))
    await db.flush()
    return {"task_id": task_id, "completed": True}


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_roadmap(current_user: CurrentUser, db: AsyncSession = Depends(get_db)) -> None:
    await db.execute(delete(RoadmapPlan).where(RoadmapPlan.user_id == current_user.id))


def _fallback_roadmap(skill_matrix: dict, hours_per_day: float) -> dict:
    weak = sorted([(s,v) for s,v in skill_matrix.items() if v<0.6], key=lambda x:x[1])
    strong = sorted([(s,v) for s,v in skill_matrix.items() if v>=0.6], key=lambda x:x[1], reverse=True)
    h = int(hours_per_day * 7)
    return {
        "summary": "Fix weaknesses first, then deepen strengths for maximum market impact.",
        "target_level": "Mid", "estimated_salary_range": "₹12L–₹25L",
        "phases": [
            {"phase":1,"title":"Phase 1 — Fix Weaknesses","goal":"Bring weak skills to 70%+","jobs_unlocked":400,
             "tasks":[{"id":f"t{i+1}","title":f"Master {s} fundamentals","description":f"Study core {s} concepts and build practice projects","skill":s,"resource_type":"course","resource_name":f"{s} - freeCodeCamp","resource_url":"https://freecodecamp.org","hours_needed":h,"priority":"high","why":f"Your {s} is {round(v*100)}% — reaching 70% unlocks significantly more jobs"} for i,(s,v) in enumerate(weak[:5])]},
            {"phase":2,"title":"Phase 2 — Deepen Strengths","goal":"Become expert in your best skills","jobs_unlocked":600,
             "tasks":[{"id":f"t{len(weak[:5])+i+1}","title":f"Advanced {s}","description":f"Advanced {s} patterns, real-world projects, performance optimization","skill":s,"resource_type":"project","resource_name":f"Build 3 real {s} projects","resource_url":"https://github.com","hours_needed":h+3,"priority":"medium","why":f"Going from {round(v*100)}% to 85%+ unlocks senior roles"} for i,(s,v) in enumerate(strong[:4])]},
            {"phase":3,"title":"Phase 3 — Market Ready","goal":"Interview prep + portfolio","jobs_unlocked":800,
             "tasks":[{"id":f"t{len(weak[:5])+len(strong[:4])+1}","title":"Build portfolio project","description":"Full-stack project showcasing all your skills","skill":strong[0][0] if strong else "Python","resource_type":"project","resource_name":"Personal Portfolio","resource_url":"https://github.com","hours_needed":h+5,"priority":"high","why":"A real project is worth more than any certificate"}]},
        ],
        "quick_wins":[{"skill":s,"action":"1 week study + take assessment","impact":"Unlock mid positions"} for s,v in weak[:3] if v>0.4],
        "career_insight":"Indian tech market rewards depth. One skill at 90% beats five skills at 50%.",
    }