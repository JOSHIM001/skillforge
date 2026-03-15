from celery import Celery
from config import settings

celery_app = Celery(
    "skillforge",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["tasks.ai_tasks", "tasks.scheduled_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    result_expires=3600,         # task results expire after 1 hour in Redis
    task_soft_time_limit=25,     # warn after 25s
    task_time_limit=30,          # hard kill after 30s (AI calls must be <30s)
    worker_prefetch_multiplier=1,  # one task at a time per worker (AI calls are heavy)
    task_acks_late=True,         # only ack after completion (safe retries)
)

# ── Celery Beat schedule (spaced repetition checker) ─────────────────────────
celery_app.conf.beat_schedule = {
    "check-spaced-repetition-daily": {
        "task": "tasks.scheduled_tasks.notify_spaced_repetition",
        "schedule": 86400,       # every 24 hours
    },
}