"""
room_service.py — Room management, team skill aggregation, role gap analysis.

Responsibilities:
  - Create/join/leave rooms (DB operations)
  - Aggregate individual skill matrices into a team matrix
  - Run AI role gap analysis with caching
  - Build the RoomStateOut that gets broadcast to all WebSocket members
"""

import random
import string
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.room import CollabRoom, RoomMember
from models.skill import SkillProfile
from models.user import User
from schemas.room import RoleGapOut, RoomMemberOut, RoomStateOut
from services.ai_service import analyze_team_gaps


# ── Room code generation ──────────────────────────────────────────────────────

def _generate_room_code(length: int = 6) -> str:
    """Generate a short uppercase alphanumeric room code e.g. 'ABC123'."""
    chars = string.ascii_uppercase + string.digits
    return "".join(random.choices(chars, k=length))


async def _unique_room_code(db: AsyncSession) -> str:
    """Keep generating until we find a code not already in the DB."""
    for _ in range(10):  # extremely unlikely to need more than 1 try
        code = _generate_room_code()
        result = await db.execute(select(CollabRoom).where(CollabRoom.id == code))
        if result.scalar_one_or_none() is None:
            return code
    raise RuntimeError("Could not generate unique room code after 10 attempts")


# ── CRUD ──────────────────────────────────────────────────────────────────────

async def create_room(
    name: str,
    created_by: uuid.UUID,
    db: AsyncSession,
) -> CollabRoom:
    code = await _unique_room_code(db)
    room = CollabRoom(id=code, name=name, created_by=created_by)
    db.add(room)

    # Creator is automatically a member
    membership = RoomMember(room_id=code, user_id=created_by)
    db.add(membership)

    await db.flush()
    return room


async def get_room(room_id: str, db: AsyncSession) -> CollabRoom | None:
    result = await db.execute(
        select(CollabRoom).where(CollabRoom.id == room_id.upper())
    )
    return result.scalar_one_or_none()


async def join_room(
    room_id: str,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> CollabRoom:
    room = await get_room(room_id, db)
    if room is None:
        raise ValueError(f"Room '{room_id}' not found")

    # Idempotent join — no error if already a member
    existing = await db.execute(
        select(RoomMember).where(
            RoomMember.room_id == room.id,
            RoomMember.user_id == user_id,
        )
    )
    if existing.scalar_one_or_none() is None:
        db.add(RoomMember(room_id=room.id, user_id=user_id))
        await db.flush()

    return room


async def leave_room(
    room_id: str,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> None:
    result = await db.execute(
        select(RoomMember).where(
            RoomMember.room_id == room_id.upper(),
            RoomMember.user_id == user_id,
        )
    )
    membership = result.scalar_one_or_none()
    if membership:
        await db.delete(membership)
        await db.flush()


# ── Member skill snapshots ────────────────────────────────────────────────────

async def _get_member_snapshots(
    room_id: str,
    db: AsyncSession,
    online_user_ids: set[str],
) -> list[RoomMemberOut]:
    """
    Load all room members with their User and SkillProfile data.
    Returns a RoomMemberOut for each member including online presence.
    """
    # Load all members with their user records
    members_result = await db.execute(
        select(RoomMember, User)
        .join(User, RoomMember.user_id == User.id)
        .where(RoomMember.room_id == room_id)
    )
    rows = members_result.all()

    snapshots = []
    for membership, user in rows:
        # Load skill profiles for this member
        profiles_result = await db.execute(
            select(SkillProfile).where(
                SkillProfile.user_id == user.id,
                SkillProfile.confidence > 0,
            )
        )
        profiles = profiles_result.scalars().all()

        # Compute overall score and find top skill
        overall_score = 0.0
        top_skill: str | None = None
        top_score = 0.0

        if profiles:
            overall_score = sum(p.score for p in profiles) / len(profiles)
            for p in profiles:
                if p.score > top_score:
                    top_score = p.score
                    top_skill = p.skill_name

        snapshots.append(RoomMemberOut(
            user_id=user.id,
            username=user.username,
            avatar_url=user.avatar_url,
            top_skill=top_skill,
            overall_score=round(overall_score, 4),
            is_online=str(user.id) in online_user_ids,
        ))

    return snapshots


# ── Team matrix aggregation ───────────────────────────────────────────────────

async def _build_team_matrix(
    room_id: str,
    db: AsyncSession,
) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
    """
    Returns (team_matrix, per_member_matrix).

    team_matrix: {skill: average_score_across_all_members}
    per_member_matrix: {username: {skill: score}}

    Only includes skills where at least one member has confidence > 0.
    Team matrix uses the MAX score per skill (best coverage), not average —
    the team's capability is defined by its strongest member per skill.
    """
    members_result = await db.execute(
        select(RoomMember, User)
        .join(User, RoomMember.user_id == User.id)
        .where(RoomMember.room_id == room_id)
    )
    rows = members_result.all()

    per_member: dict[str, dict[str, float]] = {}
    skill_scores: dict[str, list[float]] = {}  # skill → list of member scores

    for membership, user in rows:
        profiles_result = await db.execute(
            select(SkillProfile).where(
                SkillProfile.user_id == user.id,
                SkillProfile.confidence > 0,
            )
        )
        profiles = profiles_result.scalars().all()

        member_skills = {p.skill_name: round(p.score, 4) for p in profiles}
        per_member[user.username] = member_skills

        for skill, score in member_skills.items():
            skill_scores.setdefault(skill, []).append(score)

    # Team capability = max score per skill across all members
    team_matrix = {
        skill: round(max(scores), 4)
        for skill, scores in skill_scores.items()
    }

    return team_matrix, per_member


# ── Role gap analysis ─────────────────────────────────────────────────────────

# Standard engineering roles and their required skills.
# Used as a fast local fallback before/after AI analysis.
_ROLE_REQUIREMENTS: dict[str, list[str]] = {
    "Backend Engineer":   ["Python", "FastAPI", "PostgreSQL", "Redis"],
    "Frontend Engineer":  ["React", "TypeScript", "JavaScript", "CSS"],
    "DevOps Engineer":    ["Docker", "Kubernetes", "AWS", "Terraform"],
    "Data Engineer":      ["Python", "PostgreSQL", "Spark", "Airflow"],
    "Security Engineer":  ["Python", "AWS", "Kubernetes", "OAuth2"],
}

_COVERAGE_THRESHOLD = 0.60  # score >= this means the role is "covered"


def _local_gap_analysis(team_matrix: dict[str, float]) -> list[RoleGapOut]:
    """
    Fast deterministic gap analysis based on _ROLE_REQUIREMENTS.
    Used as the immediate response while AI analysis is cached asynchronously.
    """
    gaps = []
    for role, required in _ROLE_REQUIREMENTS.items():
        scores = [team_matrix.get(skill, 0.0) for skill in required]
        covered_skills = [s for s in scores if s >= _COVERAGE_THRESHOLD]
        coverage_score = len(covered_skills) / len(required) if required else 0.0
        covered = coverage_score >= 0.5  # at least half the skills covered

        gaps.append(RoleGapOut(
            role=role,
            required_skills=required,
            covered=covered,
            covered_by=None,  # local analysis can't identify who covers it
            coverage_score=round(coverage_score, 2),
        ))

    return sorted(gaps, key=lambda g: (g.covered, g.coverage_score))


async def _ai_gap_analysis(
    per_member: dict[str, dict[str, float]],
    team_matrix: dict[str, float],
) -> list[RoleGapOut]:
    """
    AI-enhanced gap analysis. Falls back to local analysis if AI fails.
    Result is cached in Redis for 15 minutes (handled in call_ai).
    """
    if not per_member:
        return _local_gap_analysis(team_matrix)

    result = await analyze_team_gaps(per_member)
    if not result or "gaps" not in result:
        return _local_gap_analysis(team_matrix)

    gaps = []
    for gap_data in result.get("gaps", []):
        role = gap_data.get("role", "Unknown")
        required = _ROLE_REQUIREMENTS.get(role, gap_data.get("missing_skills", []))
        covered = gap_data.get("covered", False)

        scores = [team_matrix.get(skill, 0.0) for skill in required]
        coverage_score = sum(scores) / len(scores) if scores else 0.0

        gaps.append(RoleGapOut(
            role=role,
            required_skills=required,
            covered=covered,
            covered_by=None,
            coverage_score=round(coverage_score, 2),
        ))

    return sorted(gaps, key=lambda g: (g.covered, g.coverage_score))


# ── Full room state ───────────────────────────────────────────────────────────

async def build_room_state(
    room: CollabRoom,
    db: AsyncSession,
    online_user_ids: set[str],
    use_ai_analysis: bool = True,
) -> RoomStateOut:
    """
    Build the complete RoomStateOut for broadcast.
    Called on:
      - GET /rooms/{id}
      - WebSocket join/leave events
      - Periodic refresh via WebSocket ping
    """
    team_matrix, per_member = await _build_team_matrix(room.id, db)
    members = await _get_member_snapshots(room.id, db, online_user_ids)

    if use_ai_analysis and team_matrix:
        role_gaps = await _ai_gap_analysis(per_member, team_matrix)
    else:
        role_gaps = _local_gap_analysis(team_matrix)

    return RoomStateOut(
        room_id=room.id,
        name=room.name,
        members=members,
        team_matrix=team_matrix,
        role_gaps=role_gaps,
        online_count=len(online_user_ids),
    )