"""
routers/bounties.py — Skill Bounties API

GET    /bounties              → list all open bounties + eligibility for current user
POST   /bounties              → create a new bounty (user-posted)
GET    /bounties/ai-generate  → AI generates 3 bounties based on user's skill matrix
POST   /bounties/{id}/claim   → attempt to claim a bounty (checks skill scores)
GET    /bounties/{id}/leaderboard → top claimants for a bounty
GET    /bounties/my-claims    → bounties the current user has claimed
"""

import uuid
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from middleware.auth_middleware import CurrentUser
from models.bounty import Bounty, BountyClaim, BountyStatus
from models.skill import SkillProfile
from models.user import User
from services.ai_service import call_ai

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class RequiredSkill(BaseModel):
    skill: str
    min_score: float = Field(..., ge=0.1, le=1.0)


class CreateBountyRequest(BaseModel):
    title: str = Field(..., min_length=5, max_length=200)
    description: str | None = Field(None, max_length=1000)
    required_skills: list[RequiredSkill] = Field(..., min_length=1, max_length=5)
    reward_label: str | None = Field(None, max_length=100)
    expires_days: int = Field(default=30, ge=1, le=90)


class RequiredSkillOut(BaseModel):
    skill: str
    min_score: float


class ClaimantOut(BaseModel):
    username: str
    avatar_url: str | None
    score: float
    claimed_at: datetime
    rank: int


class BountyOut(BaseModel):
    id: uuid.UUID
    title: str
    description: str | None
    posted_by_username: str | None
    is_ai_generated: bool
    required_skills: list[RequiredSkillOut]
    reward_label: str | None
    status: str
    expires_at: datetime | None
    created_at: datetime
    claim_count: int
    top_claimant: str | None
    # Current user's eligibility
    user_eligible: bool
    user_claimed: bool
    user_scores: dict[str, float]   # {skill: current_score}
    user_composite: float            # average score across required skills


class ClaimOut(BaseModel):
    success: bool
    composite_score: float
    message: str
    rank: int | None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_user_skill_scores(
    user_id: uuid.UUID,
    skills: list[str],
    db: AsyncSession,
) -> dict[str, float]:
    """Get current skill scores for a list of skills."""
    if not skills:
        return {}
    result = await db.execute(
        select(SkillProfile).where(
            SkillProfile.user_id == user_id,
            SkillProfile.skill_name.in_(skills),
        )
    )
    profiles = result.scalars().all()
    return {p.skill_name: round(p.score, 4) for p in profiles}


async def _enrich_bounty(
    bounty: Bounty,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> BountyOut:
    """Add eligibility info to a bounty for the current user."""
    required = [RequiredSkillOut(skill=r["skill"], min_score=r["min_score"]) for r in bounty.required_skills]
    skill_names = [r["skill"] for r in bounty.required_skills]

    user_scores = await _get_user_skill_scores(user_id, skill_names, db)
    composite = sum(user_scores.get(s, 0.0) for s in skill_names) / len(skill_names) if skill_names else 0.0
    eligible = all(user_scores.get(r["skill"], 0.0) >= r["min_score"] for r in bounty.required_skills)

    # Check if already claimed
    claim_result = await db.execute(
        select(BountyClaim).where(BountyClaim.bounty_id == bounty.id, BountyClaim.user_id == user_id)
    )
    user_claim = claim_result.scalar_one_or_none()

    # Claim count + top claimant
    claims_result = await db.execute(
        select(BountyClaim).where(BountyClaim.bounty_id == bounty.id).order_by(BountyClaim.score.desc())
    )
    all_claims = claims_result.scalars().all()
    top_claimant = None
    if all_claims:
        top_user = await db.get(User, all_claims[0].user_id)
        top_claimant = top_user.username if top_user else None

    poster_username = None
    if bounty.posted_by:
        poster = await db.get(User, bounty.posted_by)
        poster_username = poster.username if poster else None

    return BountyOut(
        id=bounty.id,
        title=bounty.title,
        description=bounty.description,
        posted_by_username=poster_username,
        is_ai_generated=bounty.is_ai_generated,
        required_skills=required,
        reward_label=bounty.reward_label,
        status=bounty.status.value,
        expires_at=bounty.expires_at,
        created_at=bounty.created_at,
        claim_count=len(all_claims),
        top_claimant=top_claimant,
        user_eligible=eligible,
        user_claimed=user_claim is not None,
        user_scores=user_scores,
        user_composite=round(composite, 4),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[BountyOut], summary="List all open bounties")
async def list_bounties(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[BountyOut]:
    result = await db.execute(
        select(Bounty)
        .where(Bounty.status == BountyStatus.open)
        .order_by(Bounty.created_at.desc())
        .limit(50)
    )
    bounties = result.scalars().all()
    return [await _enrich_bounty(b, current_user.id, db) for b in bounties]


@router.post("", response_model=BountyOut, status_code=status.HTTP_201_CREATED, summary="Post a new bounty")
async def create_bounty(
    body: CreateBountyRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> BountyOut:
    bounty = Bounty(
        title=body.title,
        description=body.description,
        posted_by=current_user.id,
        is_ai_generated=False,
        required_skills=[{"skill": r.skill, "min_score": r.min_score} for r in body.required_skills],
        reward_label=body.reward_label,
        status=BountyStatus.open,
        expires_at=datetime.now(timezone.utc) + timedelta(days=body.expires_days),
    )
    db.add(bounty)
    await db.flush()
    return await _enrich_bounty(bounty, current_user.id, db)


@router.get("/ai-generate", response_model=list[BountyOut], summary="AI generates bounties based on your skill matrix")
async def ai_generate_bounties(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[BountyOut]:
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
        skill_matrix = {"Python": 0.5, "JavaScript": 0.5}

    system = (
        "You are a tech recruiter creating skill challenge bounties for developers. "
        "Generate exactly 3 diverse bounties based on the developer's skill matrix. "
        "Each bounty should be achievable but challenging. "
        "Return ONLY valid JSON: "
        '{"bounties": [{"title": "string", "description": "string", '
        '"required_skills": [{"skill": "string", "min_score": 0.7}], '
        '"reward_label": "string"}]}'
    )
    prompt = (
        f"Developer skill matrix: {skill_matrix}\n\n"
        "Create 3 bounties:\n"
        "1. One they can claim RIGHT NOW (skills just above their current scores)\n"
        "2. One that's a STRETCH (requires improving 1-2 skills by 10-20%)\n"
        "3. One that's ASPIRATIONAL (requires significant growth)\n"
        "Make reward labels creative: 'Senior Dev Badge', 'Interview Fast-Track', 'OSS Contributor', etc.\n"
        "Return ONLY valid JSON."
    )

    ai_result = await call_ai(prompt, system, cache_ttl=300)

    bounties_data = ai_result.get("bounties", []) if ai_result else []

    # Fallback if AI fails
    if not bounties_data:
        top_skills = sorted(skill_matrix.items(), key=lambda x: x[1], reverse=True)[:2]
        bounties_data = [
            {
                "title": f"{top_skills[0][0]} Practitioner Challenge",
                "description": f"Demonstrate solid {top_skills[0][0]} knowledge",
                "required_skills": [{"skill": top_skills[0][0], "min_score": min(top_skills[0][1] + 0.1, 0.9)}],
                "reward_label": "Skill Badge",
            }
        ]

    # Save AI bounties to DB
    created = []
    for b_data in bounties_data[:3]:
        req_skills = b_data.get("required_skills", [])
        if not req_skills:
            continue
        bounty = Bounty(
            title=b_data.get("title", "AI Bounty"),
            description=b_data.get("description"),
            posted_by=None,
            is_ai_generated=True,
            required_skills=[{"skill": r["skill"], "min_score": float(r["min_score"])} for r in req_skills],
            reward_label=b_data.get("reward_label", "Achievement"),
            status=BountyStatus.open,
            expires_at=datetime.now(timezone.utc) + timedelta(days=14),
        )
        db.add(bounty)
        await db.flush()
        created.append(await _enrich_bounty(bounty, current_user.id, db))

    return created


@router.post("/{bounty_id}/claim", response_model=ClaimOut, summary="Attempt to claim a bounty")
async def claim_bounty(
    bounty_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ClaimOut:
    # Load bounty
    bounty = await db.get(Bounty, bounty_id)
    if not bounty:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Bounty not found")
    if bounty.status != BountyStatus.open:
        raise HTTPException(status.HTTP_409_CONFLICT, "Bounty is closed")

    # Check already claimed
    existing = await db.execute(
        select(BountyClaim).where(BountyClaim.bounty_id == bounty_id, BountyClaim.user_id == current_user.id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "You already claimed this bounty")

    # Get user's scores for required skills
    skill_names = [r["skill"] for r in bounty.required_skills]
    user_scores = await _get_user_skill_scores(current_user.id, skill_names, db)

    # Check eligibility
    unmet = []
    for req in bounty.required_skills:
        skill = req["skill"]
        user_score = user_scores.get(skill, 0.0)
        if user_score < req["min_score"]:
            unmet.append(f"{skill} (need {round(req['min_score']*100)}%, have {round(user_score*100)}%)")

    if unmet:
        return ClaimOut(
            success=False,
            composite_score=sum(user_scores.get(r["skill"], 0.0) for r in bounty.required_skills) / len(bounty.required_skills),
            message=f"Not eligible yet. Missing: {', '.join(unmet)}",
            rank=None,
        )

    # Create claim
    composite = sum(user_scores.get(r["skill"], 0.0) for r in bounty.required_skills) / len(bounty.required_skills)
    claim = BountyClaim(
        bounty_id=bounty_id,
        user_id=current_user.id,
        score=round(composite, 4),
    )
    db.add(claim)
    await db.flush()

    # Calculate rank
    all_claims = await db.execute(
        select(BountyClaim).where(BountyClaim.bounty_id == bounty_id).order_by(BountyClaim.score.desc())
    )
    claims_list = all_claims.scalars().all()
    rank = next((i + 1 for i, c in enumerate(claims_list) if c.user_id == current_user.id), 1)

    return ClaimOut(
        success=True,
        composite_score=round(composite, 4),
        message=f"Bounty claimed! You ranked #{rank} with a {round(composite*100)}% composite score.",
        rank=rank,
    )


@router.get("/{bounty_id}/leaderboard", response_model=list[ClaimantOut], summary="Leaderboard for a bounty")
async def bounty_leaderboard(
    bounty_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[ClaimantOut]:
    result = await db.execute(
        select(BountyClaim)
        .where(BountyClaim.bounty_id == bounty_id)
        .order_by(BountyClaim.score.desc())
        .limit(20)
    )
    claims = result.scalars().all()

    output = []
    for rank, claim in enumerate(claims, 1):
        user = await db.get(User, claim.user_id)
        if user:
            output.append(ClaimantOut(
                username=user.username,
                avatar_url=user.avatar_url,
                score=claim.score,
                claimed_at=claim.claimed_at,
                rank=rank,
            ))
    return output


@router.get("/my-claims", response_model=list[BountyOut], summary="Bounties you have claimed")
async def my_claims(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[BountyOut]:
    result = await db.execute(
        select(Bounty)
        .join(BountyClaim, BountyClaim.bounty_id == Bounty.id)
        .where(BountyClaim.user_id == current_user.id)
        .order_by(BountyClaim.claimed_at.desc())
    )
    bounties = result.scalars().all()
    return [await _enrich_bounty(b, current_user.id, db) for b in bounties]