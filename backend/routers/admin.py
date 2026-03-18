"""
routers/admin.py — Admin-only endpoints.
All routes require is_admin=True on the authenticated user.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from middleware.auth_middleware import CurrentUser
from models.user import User
from models.skill import SkillProfile
from redis_client import cache_get, cache_set, cache_delete

router = APIRouter()

# ── Feature flag keys stored in Redis ────────────────────────────────────────
FEATURE_KEYS = {
    "assessment":      "feature:assessment",
    "career_gps":      "feature:career_gps",
    "team_hub":        "feature:team_hub",
    "project_analyzer":"feature:project_analyzer",
    "skill_decay":     "feature:skill_decay",
    "skill_dna":       "feature:skill_dna",
    "bounties":        "feature:bounties",
}


async def require_admin(current_user: CurrentUser, db: AsyncSession = Depends(get_db)) -> User:
    """Dependency — raises 403 if user is not admin."""
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()
    if not user or not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return user


# ── Admin login check ─────────────────────────────────────────────────────────
@router.get("/check")
async def admin_check(admin: User = Depends(require_admin)):
    """Verify the token belongs to an admin user."""
    return {"is_admin": True, "username": admin.username}


# ── Users list ────────────────────────────────────────────────────────────────
@router.get("/users")
async def list_users(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Return all users with their skill summaries."""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()

    user_list = []
    for u in users:
        # Get skill summary for each user
        skill_result = await db.execute(
            select(SkillProfile).where(SkillProfile.user_id == u.id)
        )
        skills = skill_result.scalars().all()
        overall_score = round(sum(s.score for s in skills) / len(skills), 3) if skills else 0.0

        user_list.append({
            "id": str(u.id),
            "username": u.username,
            "email": u.email,
            "is_admin": u.is_admin,
            "is_blocked": u.is_blocked,
            "created_at": u.created_at.isoformat(),
            "skills_count": len(skills),
            "overall_score": overall_score,
            "top_skill": max(skills, key=lambda s: s.score).skill_name if skills else None,
        })

    return {"users": user_list, "total": len(user_list)}


# ── Block / Unblock user ──────────────────────────────────────────────────────
@router.patch("/users/{user_id}/block")
async def block_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Block a user — they will get 403 on all API calls."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="Cannot block another admin")
    user.is_blocked = True
    await db.commit()
    return {"message": f"User {user.username} blocked", "is_blocked": True}


@router.patch("/users/{user_id}/unblock")
async def unblock_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Unblock a previously blocked user."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_blocked = False
    await db.commit()
    return {"message": f"User {user.username} unblocked", "is_blocked": False}


# ── Delete user ───────────────────────────────────────────────────────────────
@router.delete("/users/{user_id}")
async def delete_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, str]:
    """Permanently delete a user and all their data (CASCADE)."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_admin:
        raise HTTPException(status_code=400, detail="Cannot delete an admin account")
    await db.delete(user)
    await db.commit()
    return {"message": f"User {user.username} deleted permanently"}


# ── Feature flags ─────────────────────────────────────────────────────────────
@router.get("/features")
async def get_features(admin: User = Depends(require_admin)) -> dict[str, Any]:
    """Get current state of all feature flags."""
    flags = {}
    for name, key in FEATURE_KEYS.items():
        cached = await cache_get(key)
        # Default to enabled (True) if flag not set
        flags[name] = cached.get("enabled", True) if cached else True
    return {"features": flags}


@router.patch("/features/{feature_name}")
async def toggle_feature(
    feature_name: str,
    body: dict[str, bool],
    admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Enable or disable a feature. Body: {"enabled": true/false}"""
    if feature_name not in FEATURE_KEYS:
        raise HTTPException(status_code=404, detail=f"Unknown feature: {feature_name}")
    enabled = body.get("enabled", True)
    key = FEATURE_KEYS[feature_name]
    await cache_set(key, {"enabled": enabled}, ttl=0)  # no expiry — persists until changed
    return {
        "feature": feature_name,
        "enabled": enabled,
        "message": f"Feature '{feature_name}' {'enabled' if enabled else 'disabled'}",
    }


# ── Stats overview ────────────────────────────────────────────────────────────
@router.get("/stats")
async def admin_stats(
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """Dashboard stats — total users, blocked, assessments, skills."""
    total_users = (await db.execute(select(func.count(User.id)))).scalar()
    blocked_users = (await db.execute(
        select(func.count(User.id)).where(User.is_blocked == True)
    )).scalar()
    total_skills = (await db.execute(select(func.count(SkillProfile.id)))).scalar()

    return {
        "total_users": total_users,
        "blocked_users": blocked_users,
        "active_users": total_users - blocked_users,
        "total_skill_profiles": total_skills,
    }