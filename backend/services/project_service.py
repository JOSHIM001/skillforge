"""
project_service.py — Project stack validation and blueprint generation.

The generic filter prevents AI responses like "authentication" or "testing"
from appearing as "missing tech" — we want specific library names only.
"""

from services.ai_service import generate_project_blueprint, validate_project_stack

# ── Generic term filter ───────────────────────────────────────────────────────
# These are concepts, not technologies — filter them from AI suggestions.
_GENERIC_TERMS: set[str] = {
    "deployment", "ci/cd", "cicd", "system design", "scalability",
    "monitoring", "documentation", "testing", "security", "performance",
    "architecture", "devops", "containerization", "best practices",
    "code review", "authentication", "authorization", "caching",
    "logging", "error handling", "validation", "database", "api",
    "frontend", "backend", "microservices", "serverless", "cloud",
    "infrastructure", "automation", "integration", "optimization",
}


def filter_generic(suggestions: list[str]) -> list[str]:
    """
    Remove generic/vague terms from AI-generated tech suggestions.
    Keeps only specific library/tool names (e.g. "Redis", "Celery", "OAuth2").
    """
    return [
        s for s in suggestions
        if s.lower().strip() not in _GENERIC_TERMS
        and len(s.strip()) > 2
        and not s.lower().startswith("add ")
        and not s.lower().startswith("use ")
        and not s.lower().startswith("implement ")
    ]


# ── Service functions ─────────────────────────────────────────────────────────
async def validate_project(project_idea: str, tech_stack: list[str]) -> dict:
    """
    Validate a project's tech stack and return specific missing technologies.
    Applies the generic filter before returning.
    """
    result = await validate_project_stack(project_idea, tech_stack)
    if result.get("missing_tech"):
        result["missing_tech"] = filter_generic(result["missing_tech"])
    return result


async def get_blueprint(skill_matrix: dict[str, float]) -> dict:
    """Generate a personalized project blueprint from the user's skill matrix."""
    return await generate_project_blueprint(skill_matrix)