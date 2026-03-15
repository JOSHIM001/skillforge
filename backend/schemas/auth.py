import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


# ── Request schemas ───────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def username_not_reserved(cls, v: str) -> str:
        reserved = {"admin", "root", "api", "www", "mail", "system"}
        if v.lower() in reserved:
            raise ValueError("Username is reserved")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


# ── Response schemas ──────────────────────────────────────────────────────────

class UserOut(BaseModel):
    id: uuid.UUID
    username: str
    email: str
    avatar_url: str | None
    github_id: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenPair(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


# ── GitHub OAuth schemas ──────────────────────────────────────────────────────

class GitHubProfile(BaseModel):
    id: int
    login: str
    email: str | None = None
    avatar_url: str | None = None
    name: str | None = None


class GitHubTokenResponse(BaseModel):
    access_token: str
    token_type: str
    scope: str = ""