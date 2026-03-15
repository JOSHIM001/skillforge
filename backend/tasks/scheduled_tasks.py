"""
scheduled_tasks.py — Celery Beat periodic tasks.

Currently:
  - notify_spaced_repetition: runs daily, finds all users with
    skills due for reassessment and publishes a Redis notification
    (in production, swap the Redis publish for an email/push service).
"""

import asyncio

from loguru import logger

from tasks.celery_app import celery_app


def _run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


@celery_app.task(name="tasks.scheduled_tasks.notify_spaced_repetition")
def notify_spaced_repetition() -> dict:
    """
    Daily task: find all users with skills due for spaced repetition review
    and publish a notification to their personal Redis channel.

    The frontend WebSocket listener receives these and surfaces a nudge.
    """
    async def _run():
        from database import AsyncSessionLocal
        from services.skill_service import get_spaced_repetition_queue
        from redis_client import redis
        from sqlalchemy import select, distinct
        from models.skill import SkillProfile

        notified = 0
        async with AsyncSessionLocal() as db:
            # Get all users who have skill profiles
            result = await db.execute(select(distinct(SkillProfile.user_id)))
            user_ids = result.scalars().all()

            for user_id in user_ids:
                queue = await get_spaced_repetition_queue(user_id, db)
                if queue:
                    import json
                    channel = f"user:{user_id}:notifications"
                    await redis.publish(channel, json.dumps({
                        "type": "spaced_repetition_reminder",
                        "skills_due": [q["skill_name"] for q in queue],
                        "count": len(queue),
                    }))
                    notified += 1

        return notified

    count = _run_async(_run())
    logger.info("Spaced repetition notifications sent", count=count)
    return {"users_notified": count}