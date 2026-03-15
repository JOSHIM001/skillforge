"""
routers/career_gps.py — Career GPS: Real-time job market intelligence

GET /career-gps/market          → job market analysis for user's skills
GET /career-gps/opportunities   → live job listings from GitHub + AI enrichment
GET /career-gps/skill-gaps      → which skills unlock the most jobs
GET /career-gps/salary-hints    → salary range estimates per skill combo
"""

import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from middleware.auth_middleware import CurrentUser
from models.skill import SkillProfile
from services.career_gps_service import (
    get_market_analysis,
    get_live_opportunities,
    get_skill_gap_analysis,
    get_salary_hints,
)
from sqlalchemy import select

router = APIRouter()


async def _get_skill_matrix(user_id: uuid.UUID, db: AsyncSession) -> dict[str, float]:
    result = await db.execute(
        select(SkillProfile).where(
            SkillProfile.user_id == user_id,
            SkillProfile.confidence > 0,
        )
    )
    profiles = result.scalars().all()
    return {p.skill_name: round(p.score, 4) for p in profiles}


@router.get("/market", summary="Full job market analysis for user's skill matrix")
async def market_analysis(
    current_user: CurrentUser,
    location: str = Query(default="remote", description="Location filter e.g. 'remote', 'USA', 'London'"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    matrix = await _get_skill_matrix(current_user.id, db)
    return await get_market_analysis(matrix, location)


@router.get("/opportunities", summary="Live job opportunities matched to skill matrix")
async def live_opportunities(
    current_user: CurrentUser,
    location: str = Query(default="remote"),
    limit: int = Query(default=15, ge=5, le=30),
    db: AsyncSession = Depends(get_db),
) -> dict:
    matrix = await _get_skill_matrix(current_user.id, db)
    return await get_live_opportunities(matrix, location, limit)


@router.get("/skill-gaps", summary="Which skills to learn to unlock the most jobs")
async def skill_gaps(
    current_user: CurrentUser,
    location: str = Query(default="remote"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    matrix = await _get_skill_matrix(current_user.id, db)
    return await get_skill_gap_analysis(matrix, location)


@router.get("/salary-hints", summary="Salary range estimates based on skill matrix")
async def salary_hints(
    current_user: CurrentUser,
    location: str = Query(default="USA"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    matrix = await _get_skill_matrix(current_user.id, db)
    return await get_salary_hints(matrix, location)