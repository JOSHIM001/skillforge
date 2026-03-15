"""
ai_tasks.py — Celery tasks for AI operations that exceed ~3 seconds.

Pattern:
  Router calls task.delay(...) → returns task_id immediately
  Frontend polls GET /tasks/{task_id}/status every 2s
  Task stores result in Redis with 1hr TTL
"""

import asyncio
import json
import uuid

from loguru import logger

from tasks.celery_app import celery_app
from redis_client import cache_set


def _run_async(coro):
    """Run an async coroutine from within a sync Celery task."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ── Question generation task ──────────────────────────────────────────────────
@celery_app.task(
    bind=True,
    name="tasks.ai_tasks.generate_question_task",
    max_retries=2,
    default_retry_delay=3,
)
def generate_question_task(
    self,
    skill: str,
    difficulty: int,
    previous_questions: list[str],
    task_result_key: str,
) -> dict:
    """
    Async-safe Celery wrapper around ai_service.generate_question.
    Stores result in Redis so the HTTP endpoint can poll for it.
    """
    try:
        from services.ai_service import generate_question
        result = _run_async(generate_question(skill, difficulty, previous_questions))
        _run_async(cache_set(task_result_key, {"status": "done", "result": result}, ttl=3600))
        return result
    except Exception as exc:
        logger.error("generate_question_task failed", error=str(exc), skill=skill)
        _run_async(cache_set(
            task_result_key,
            {"status": "error", "detail": str(exc)},
            ttl=3600,
        ))
        raise self.retry(exc=exc)


# ── Answer grading task ───────────────────────────────────────────────────────
@celery_app.task(
    bind=True,
    name="tasks.ai_tasks.grade_answer_task",
    max_retries=2,
    default_retry_delay=3,
)
def grade_answer_task(
    self,
    question: str,
    user_answer: str,
    expected_topics: list[str],
    skill: str,
    task_result_key: str,
) -> dict:
    """
    Grades an answer and stores the result in Redis.
    The router submits this task and immediately returns the task_id.
    """
    try:
        from services.ai_service import grade_answer
        result = _run_async(grade_answer(question, user_answer, expected_topics, skill))
        _run_async(cache_set(task_result_key, {"status": "done", "result": result}, ttl=3600))
        return result
    except Exception as exc:
        logger.error("grade_answer_task failed", error=str(exc))
        _run_async(cache_set(
            task_result_key,
            {"status": "error", "detail": str(exc)},
            ttl=3600,
        ))
        raise self.retry(exc=exc)


# ── Project analysis task ─────────────────────────────────────────────────────
@celery_app.task(
    bind=True,
    name="tasks.ai_tasks.analyze_project_task",
    max_retries=1,
)
def analyze_project_task(
    self,
    project_idea: str,
    tech_stack: list[str],
    task_result_key: str,
) -> dict:
    try:
        from services.ai_service import validate_project_stack
        from services.project_service import filter_generic
        result = _run_async(validate_project_stack(project_idea, tech_stack))
        if result.get("missing_tech"):
            result["missing_tech"] = filter_generic(result["missing_tech"])
        _run_async(cache_set(task_result_key, {"status": "done", "result": result}, ttl=3600))
        return result
    except Exception as exc:
        logger.error("analyze_project_task failed", error=str(exc))
        _run_async(cache_set(
            task_result_key,
            {"status": "error", "detail": str(exc)},
            ttl=3600,
        ))
        raise self.retry(exc=exc)


# ── Task result key helper (used by routers) ──────────────────────────────────
def make_task_key(task_id: str) -> str:
    return f"task:result:{task_id}"