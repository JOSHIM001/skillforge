"""
celery_app.py — Production Celery config with retry logic
"""

from celery import Celery
from config import settings

celery_app = Celery(
    "skillforge",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["tasks.ai_tasks", "tasks.scheduled_tasks"],
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,

    # Task execution
    task_track_started=True,
    result_expires=3600,
    task_soft_time_limit=25,
    task_time_limit=35,
    worker_prefetch_multiplier=1,
    task_acks_late=True,

    # ── Retry configuration ────────────────────────────────────────────────
    # Auto-retry on these exceptions (Groq API failures, network issues)
    task_autoretry_for=(Exception,),
    task_max_retries=3,
    task_retry_backoff=True,          # Exponential backoff: 1s, 2s, 4s
    task_retry_backoff_max=60,        # Cap at 60s between retries
    task_retry_jitter=True,           # Add randomness to prevent thundering herd

    # ── Dead letter queue ──────────────────────────────────────────────────
    # Tasks that fail all retries go to a dead letter queue for inspection
    task_reject_on_worker_lost=True,
    task_default_queue="default",
    task_queues={
        "default": {"exchange": "default", "routing_key": "default"},
        "ai_heavy": {"exchange": "ai_heavy", "routing_key": "ai_heavy"},
        "dead_letter": {"exchange": "dead_letter", "routing_key": "dead_letter"},
    },

    # ── Worker health ──────────────────────────────────────────────────────
    worker_max_tasks_per_child=50,     # Restart worker after 50 tasks (memory leak prevention)
    worker_max_memory_per_child=512000, # Restart if worker uses >512MB

    # ── Monitoring ────────────────────────────────────────────────────────
    task_send_sent_event=True,
    worker_send_task_events=True,
)

celery_app.conf.beat_schedule = {
    "check-spaced-repetition-daily": {
        "task": "tasks.scheduled_tasks.notify_spaced_repetition",
        "schedule": 86400,
    },
}