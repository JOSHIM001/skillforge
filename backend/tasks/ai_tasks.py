"""
tasks/ai_tasks.py — Celery tasks with retry logic, input sanitization, request tracing
"""

import asyncio
import uuid

from loguru import logger

from tasks.celery_app import celery_app
from redis_client import cache_set


def _run_async(coro):
    """Run async coroutine from sync Celery task."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _sanitize(text: str, max_len: int = 2000) -> str:
    """Strip control chars, truncate, neutralize prompt injections."""
    import re
    if not text:
        return ""
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    text = text[:max_len]
    injection_patterns = [
        r"ignore\s+(all\s+)?previous\s+instructions",
        r"give\s+me\s+(100|full|perfect)\s+(score|grade)",
        r"mark\s+(my\s+answer|this)\s+as\s+correct",
        r"you\s+must\s+(give|award|grant)",
        r"override\s+(your\s+)?instructions",
        r"<\s*system\s*>",
        r"\[INST\]",
    ]
    for p in injection_patterns:
        if re.search(p, text, re.IGNORECASE):
            logger.warning("Prompt injection attempt in task input", preview=text[:80])
            text = f"[USER INPUT]: {text}"
            break
    return text


# ── Question generation ───────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="tasks.ai_tasks.generate_question_task",
    max_retries=3,
    default_retry_delay=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=30,
    retry_jitter=True,
)
def generate_question_task(
    self,
    skill: str,
    difficulty: int,
    previous_questions: list[str],
    task_result_key: str,
    request_id: str = "",
) -> dict:
    task_id = self.request.id or str(uuid.uuid4())[:8]
    with logger.contextualize(task_id=task_id, request_id=request_id, skill=skill):
        try:
            logger.info("Generating question", difficulty=difficulty, attempt=self.request.retries + 1)
            skill_clean = _sanitize(skill, 100)
            prev_clean = [_sanitize(q, 500) for q in (previous_questions or [])]

            from services.ai_service import generate_question
            result = _run_async(generate_question(skill_clean, difficulty, prev_clean))

            _run_async(cache_set(task_result_key, {"status": "done", "result": result}, ttl=3600))
            logger.info("Question generated successfully")
            return result

        except Exception as exc:
            attempt = self.request.retries + 1
            logger.error("generate_question_task failed", error=str(exc), attempt=attempt)

            if self.request.retries >= self.max_retries:
                # Final failure — store error result
                _run_async(cache_set(
                    task_result_key,
                    {"status": "error", "detail": "Question generation failed after retries"},
                    ttl=3600,
                ))
            raise


# ── Answer grading ────────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="tasks.ai_tasks.grade_answer_task",
    max_retries=3,
    default_retry_delay=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=30,
    retry_jitter=True,
)
def grade_answer_task(
    self,
    question: str,
    user_answer: str,
    expected_topics: list[str],
    skill: str,
    task_result_key: str,
    request_id: str = "",
) -> dict:
    task_id = self.request.id or str(uuid.uuid4())[:8]
    with logger.contextualize(task_id=task_id, request_id=request_id, skill=skill):
        try:
            logger.info("Grading answer", answer_len=len(user_answer or ""), attempt=self.request.retries + 1)

            # Sanitize user answer before sending to AI
            answer_clean = _sanitize(user_answer, 2000)
            question_clean = _sanitize(question, 1000)

            from services.ai_service import grade_answer
            result = _run_async(grade_answer(question_clean, answer_clean, expected_topics, skill))

            _run_async(cache_set(task_result_key, {"status": "done", "result": result}, ttl=3600))
            logger.info("Answer graded", score=result.get("score"))
            return result

        except Exception as exc:
            attempt = self.request.retries + 1
            logger.error("grade_answer_task failed", error=str(exc), attempt=attempt)

            if self.request.retries >= self.max_retries:
                _run_async(cache_set(
                    task_result_key,
                    {"status": "error", "detail": "Grading failed — please try again"},
                    ttl=3600,
                ))
            raise


# ── Project analysis ──────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="tasks.ai_tasks.analyze_project_task",
    max_retries=2,
    default_retry_delay=5,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def analyze_project_task(
    self,
    project_idea: str,
    tech_stack: list[str],
    task_result_key: str,
    request_id: str = "",
) -> dict:
    task_id = self.request.id or str(uuid.uuid4())[:8]
    with logger.contextualize(task_id=task_id, request_id=request_id):
        try:
            logger.info("Analyzing project", attempt=self.request.retries + 1)

            idea_clean = _sanitize(project_idea, 500)
            stack_clean = [_sanitize(t, 50) for t in (tech_stack or [])]

            from services.ai_service import validate_project_stack
            from services.project_service import filter_generic
            result = _run_async(validate_project_stack(idea_clean, stack_clean))
            if result.get("missing_tech"):
                result["missing_tech"] = filter_generic(result["missing_tech"])

            _run_async(cache_set(task_result_key, {"status": "done", "result": result}, ttl=3600))
            logger.info("Project analyzed successfully")
            return result

        except Exception as exc:
            logger.error("analyze_project_task failed", error=str(exc))
            if self.request.retries >= self.max_retries:
                _run_async(cache_set(
                    task_result_key,
                    {"status": "error", "detail": "Analysis failed — please try again"},
                    ttl=3600,
                ))
            raise


def make_task_key(task_id: str) -> str:
    return f"task:result:{task_id}"