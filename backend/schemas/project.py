from pydantic import BaseModel, Field


class ValidateProjectRequest(BaseModel):
    idea: str = Field(..., min_length=10, max_length=2000)
    tech_stack: list[str] = Field(default_factory=list, max_length=30)


class ValidationResultOut(BaseModel):
    missing_tech: list[str]
    reasoning: str


class BlueprintOut(BaseModel):
    title: str
    description: str
    tech_stack: list[str]
    skills_practiced: list[str]
    estimated_days: int


class IssueOut(BaseModel):
    id: int
    title: str
    url: str
    repo: str
    label: str           # "help-wanted" | "good-first-issue"
    language: str
    skills: list[str]
    stars: int
    comments: int
    match: int           # 0–100 match score vs user's skill matrix
    created_at: str