"""
routers/admin.py — Admin-only endpoints.
Admin is identified by ADMIN_EMAIL environment variable.
No DB migration needed — no is_admin column required.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from middleware.auth_middleware import CurrentUser
from models.user import User
from models.skill import SkillProfile
from redis_client import cache_get, cache_set

router = APIRouter()

# ── Feature flag keys stored in Redis ────────────────────────────────────────
FEATURE_KEYS = {
    "assessment":       "feature:assessment",
    "career_gps":       "feature:career_gps",
    "team_hub":         "feature:team_hub",
    "project_analyzer": "feature:project_analyzer",
    "skill_decay":      "feature:skill_decay",
    "skill_dna":        "feature:skill_dna",
    "bounties":         "feature:bounties",
}

# Blocked users list stored in Redis as a set
BLOCKED_KEY = "admin:blocked_users"


async def require_admin(current_user: CurrentUser) -> User:
    """Dependency — raises 403 if user email doesn't match ADMIN_EMAIL."""
    admin_email = getattr(settings, "admin_email", "")
    if not admin_email or current_user.email.lower() != admin_email.lower():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )
    return current_user


# ── Admin login check ─────────────────────────────────────────────────────────
@router.get("/check")
async def admin_check(admin: User = Depends(require_admin)):
    """Verify the token belongs to an admin user."""
    return {"is_admin": True, "username": admin.username, "email": admin.email}


# ── Stats overview ────────────────────────────────────────────────────────────
@router.get("/stats")
async def admin_stats(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, Any]:
    total_users  = (await db.execute(select(func.count(User.id)))).scalar()
    total_skills = (await db.execute(select(func.count(SkillProfile.id)))).scalar()

    # Blocked list from Redis
    blocked_raw = await cache_get(BLOCKED_KEY)
    blocked_ids: list = blocked_raw.get("ids", []) if blocked_raw else []

    return {
        "total_users":          total_users,
        "blocked_users":        len(blocked_ids),
        "active_users":         total_users - len(blocked_ids),
        "total_skill_profiles": total_skills,
    }


# ── Users list ────────────────────────────────────────────────────────────────
@router.get("/users")
async def list_users(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, Any]:
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users  = result.scalars().all()

    # Get blocked list from Redis
    blocked_raw = await cache_get(BLOCKED_KEY)
    blocked_ids: list = blocked_raw.get("ids", []) if blocked_raw else []
    admin_email = getattr(settings, "admin_email", "").lower()

    user_list = []
    for u in users:
        skill_result = await db.execute(
            select(SkillProfile).where(SkillProfile.user_id == u.id)
        )
        skills = skill_result.scalars().all()
        overall = round(sum(s.score for s in skills) / len(skills), 3) if skills else 0.0

        user_list.append({
            "id":            str(u.id),
            "username":      u.username,
            "email":         u.email,
            "is_admin":      u.email.lower() == admin_email,
            "is_blocked":    str(u.id) in blocked_ids,
            "created_at":    u.created_at.isoformat(),
            "skills_count":  len(skills),
            "overall_score": overall,
            "top_skill":     max(skills, key=lambda s: s.score).skill_name if skills else None,
        })

    return {"users": user_list, "total": len(user_list)}


# ── Block / Unblock (stored in Redis — no DB column needed) ──────────────────
@router.patch("/users/{user_id}/block")
async def block_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, Any]:
    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    admin_email = getattr(settings, "admin_email", "").lower()
    if user.email.lower() == admin_email:
        raise HTTPException(status_code=400, detail="Cannot block the admin account")

    blocked_raw = await cache_get(BLOCKED_KEY)
    blocked_ids: list = blocked_raw.get("ids", []) if blocked_raw else []
    uid = str(user_id)
    if uid not in blocked_ids:
        blocked_ids.append(uid)
    await cache_set(BLOCKED_KEY, {"ids": blocked_ids}, ttl=0)
    return {"message": f"User {user.username} blocked", "is_blocked": True}


@router.patch("/users/{user_id}/unblock")
async def unblock_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, Any]:
    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    blocked_raw = await cache_get(BLOCKED_KEY)
    blocked_ids: list = blocked_raw.get("ids", []) if blocked_raw else []
    blocked_ids = [i for i in blocked_ids if i != str(user_id)]
    await cache_set(BLOCKED_KEY, {"ids": blocked_ids}, ttl=0)
    return {"message": f"User {user.username} unblocked", "is_blocked": False}


# ── Delete user ───────────────────────────────────────────────────────────────
@router.delete("/users/{user_id}")
async def delete_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, str]:
    result = await db.execute(select(User).where(User.id == user_id))
    user   = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    admin_email = getattr(settings, "admin_email", "").lower()
    if user.email.lower() == admin_email:
        raise HTTPException(status_code=400, detail="Cannot delete the admin account")

    await db.delete(user)
    await db.commit()
    return {"message": f"User {user.username} deleted permanently"}


# ── Feature flags (Redis) ─────────────────────────────────────────────────────
@router.get("/features")
async def get_features() -> dict[str, Any]:
    """Public endpoint — any user can read flags to show/hide features."""
    flags = {}
    for name, key in FEATURE_KEYS.items():
        cached = await cache_get(key)
        flags[name] = cached.get("enabled", True) if cached else True
    return {"features": flags}


@router.patch("/features/{feature_name}")
async def toggle_feature(
    feature_name: str,
    body: dict[str, bool],
    admin: User = Depends(require_admin),
) -> dict[str, Any]:
    if feature_name not in FEATURE_KEYS:
        raise HTTPException(status_code=404, detail=f"Unknown feature: {feature_name}")
    enabled = body.get("enabled", True)
    await cache_set(FEATURE_KEYS[feature_name], {"enabled": enabled}, ttl=0)
    return {
        "feature": feature_name,
        "enabled": enabled,
        "message": f"Feature '{feature_name}' {'enabled' if enabled else 'disabled'}",
    }