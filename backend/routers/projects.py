from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from middleware.auth_middleware import CurrentUser
from schemas.project import BlueprintOut, ValidateProjectRequest, ValidationResultOut
from services.project_service import get_blueprint, validate_project
from services.skill_service import get_skill_matrix

router = APIRouter()


@router.post(
    "/validate",
    response_model=ValidationResultOut,
    summary="Analyse a project idea for missing technologies",
)
async def validate_project_stack(
    body: ValidateProjectRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> ValidationResultOut:
    result = await validate_project(body.idea, body.tech_stack)
    return ValidationResultOut(**result)


@router.get(
    "/blueprint",
    response_model=BlueprintOut,
    summary="Generate a personalised project blueprint from the user's skill matrix",
)
async def get_project_blueprint(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> BlueprintOut:
    matrix = await get_skill_matrix(current_user.id, db)

    result = await get_blueprint(matrix)

    return BlueprintOut(
        title=result.get("title", "Portfolio Project"),
        description=result.get("description", ""),
        tech_stack=result.get("tech_stack", []),
        skills_practiced=result.get("skills_practiced", []),
        estimated_days=result.get("estimated_days", 14),
    )