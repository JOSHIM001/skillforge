from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # ── Database ──────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/skillapp"

    # ── Redis ─────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ── JWT Auth ──────────────────────────────────────────────
    secret_key: str = "change-this-to-a-random-64-char-string-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # ── AI Providers ──────────────────────────────────────────
    groq_api_key: str = ""
    openai_api_key: str = ""  # optional fallback

    # ── GitHub OAuth ──────────────────────────────────────────
    github_client_id: str = ""
    github_client_secret: str = ""

    # ── App ───────────────────────────────────────────────────
    frontend_url: str = "http://localhost:8000"
    environment: str = "development"  # development | production

    # ── Admin ─────────────────────────────────────────────────
    admin_email: str = ""  # Set in Render env vars — this email gets admin access

    # ── Observability ─────────────────────────────────────────
    sentry_dsn: str = ""

    # ── Rate limiting ─────────────────────────────────────────
    rate_limit_ai: str = "10/minute"
    rate_limit_general: str = "60/minute"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def cors_origins(self) -> list[str]:
        if self.is_production:
            return [self.frontend_url]
        return ["http://localhost:3000", "http://localhost:8000", self.frontend_url]


@lru_cache()
def get_settings() -> Settings:
    """Cached settings instance — import this everywhere."""
    return Settings()


settings = get_settings()