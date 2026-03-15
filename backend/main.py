import logging
import os
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from config import settings
from database import close_db, init_db
from middleware.rate_limiter import limiter
from redis_client import close_redis, ping_redis

# ── Sentry (optional) ─────────────────────────────────────────────────────────
if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        traces_sample_rate=0.2,
        send_default_pii=False,
    )

# ── Loguru ────────────────────────────────────────────────────────────────────
logging.basicConfig(handlers=[logging.StreamHandler()], level=logging.WARNING)
logger.add(
    "logs/app.log",
    rotation="10 MB",
    retention="7 days",
    level="INFO",
    serialize=True,
    enqueue=True,
)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting SkillForge API", environment=settings.environment)

    if not await ping_redis():
        logger.error("Redis unreachable on startup — check REDIS_URL")

    await init_db()
    logger.info("Database tables verified/created")

    yield

    logger.info("Shutting down SkillForge API")
    await close_db()
    await close_redis()


# ── App factory ───────────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    app = FastAPI(
        title="SkillForge API",
        description="AI-Driven Skill Assessment & Career Recommendation Platform",
        version="1.0.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── Rate limiter ──────────────────────────────────────────────
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # ── CORS ──────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────
    from routers.auth           import router as auth_router
    from routers.skills         import router as skills_router
    from routers.projects       import router as projects_router
    from routers.github         import router as github_router
    from routers.rooms          import router as rooms_router
    from routers.bounties       import router as bounties_router
    from routers.career_gps     import router as gps_router
    from routers.ghost_interview import router as ghost_router
    from websocket.ws_router    import router as ws_router
            
    app.include_router(auth_router,     prefix="/api/auth",           tags=["Auth"])
    app.include_router(skills_router,   prefix="/api/skills",         tags=["Skills"])
    app.include_router(projects_router, prefix="/api/projects",       tags=["Projects"])
    app.include_router(github_router,   prefix="/api/github",         tags=["GitHub"])
    app.include_router(rooms_router,    prefix="/api/rooms",          tags=["Rooms"])
    app.include_router(bounties_router, prefix="/api/bounties",       tags=["Bounties"])
    app.include_router(gps_router,      prefix="/api/career-gps",     tags=["Career GPS"])
    app.include_router(ghost_router,    prefix="/api/ghost-interview", tags=["Ghost Interview"])
    app.include_router(ws_router,                                      tags=["WebSocket"])

    # ── Serve frontend ────────────────────────────────────────────
    frontend_path = os.path.join(os.path.dirname(__file__), "frontend")
    if os.path.exists(frontend_path):
        app.mount("/static", StaticFiles(directory=frontend_path), name="static")

        @app.get("/", include_in_schema=False)
        async def serve_frontend():
            return FileResponse(os.path.join(frontend_path, "index.html"))

    # ── Health ────────────────────────────────────────────────────
    @app.get("/health", tags=["Infra"])
    async def health_check():
        redis_ok = await ping_redis()
        return {
            "status":   "ok" if redis_ok else "degraded",
            "database": "ok",
            "redis":    "ok" if redis_ok else "unreachable",
            "version":  "1.0.0",
            "env":      settings.environment,
        }

    # ── Global error handler ──────────────────────────────────────
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={"detail": "An unexpected error occurred."},
        )

    return app


# ── Entry point ───────────────────────────────────────────────────────────────
app = create_app()