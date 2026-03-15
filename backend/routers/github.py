from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from middleware.auth_middleware import CurrentUser
from schemas.project import IssueOut
from services.github_service import search_help_wanted_issues
from services.skill_service import get_skill_matrix

router = APIRouter()


@router.get(
    "/issues",
    response_model=list[IssueOut],
    summary="Get help-wanted GitHub issues matched to the user's skill matrix",
)
async def get_matching_issues(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    label: str = Query("help wanted", description="Issue label filter"),
    limit: int = Query(20, ge=1, le=50),
) -> list[IssueOut]:
    matrix = await get_skill_matrix(current_user.id, db)

    # Use top skills by score (highest proficiency first)
    top_skills = sorted(matrix.keys(), key=lambda s: matrix[s], reverse=True)[:8]

    issues = await search_help_wanted_issues(
        skills=top_skills,
        label=label,
        max_results=limit,
    )
    return [IssueOut(**i) for i in issues]


@router.get(
    "/scan",
    summary="Scan the user's GitHub repos to pre-seed their skill matrix",
    response_model=dict,
)
async def scan_github_repos(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    github_token: str = Query(..., description="GitHub OAuth access token"),
) -> dict:
    """
    Called immediately after GitHub OAuth login.
    Returns {skill_name: seed_score} for skills inferred from repo languages.
    The frontend uses this to display a pre-populated (low-confidence) skill matrix
    before the user completes any assessments.
    """
    from services.github_service import scan_user_repos
    seeds = await scan_user_repos(github_token)
    return {"seeded_skills": seeds, "count": len(seeds)}