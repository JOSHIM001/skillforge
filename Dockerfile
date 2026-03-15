# ═══════════════════════════════════════════════════════════════
# Stage 1 — dependency builder
# Installs all Python packages into a virtual environment.
# Kept separate so the final image doesn't need build tools.
# ═══════════════════════════════════════════════════════════════
FROM python:3.11-slim AS builder

# Build-time deps for packages with C extensions (asyncpg, bcrypt, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy requirements first — Docker layer cache means this only
# re-runs when requirements.txt actually changes.
COPY backend/requirements.txt .

# Install into an isolated venv so we can copy just the venv to the final stage
RUN python -m venv /venv && \
    /venv/bin/pip install --upgrade pip && \
    /venv/bin/pip install --no-cache-dir -r requirements.txt


# ═══════════════════════════════════════════════════════════════
# Stage 2 — production runtime
# Minimal image: no build tools, no apt cache, no pip cache.
# ═══════════════════════════════════════════════════════════════
FROM python:3.11-slim AS runtime

# Runtime-only system deps (libpq for asyncpg at runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user — never run production containers as root
RUN groupadd --gid 1001 appgroup && \
    useradd  --uid 1001 --gid appgroup --no-create-home appuser

WORKDIR /app

# Copy the venv from the builder stage — no pip needed at runtime
COPY --from=builder /venv /venv

# Copy application code
COPY backend/ .

# Create log directory and own everything as appuser
RUN mkdir -p /app/logs && chown -R appuser:appgroup /app

USER appuser

# Activate venv by prepending to PATH
ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# ── Health check ──────────────────────────────────────────────
# Docker / Compose will mark the container unhealthy if /health
# returns non-200 for 3 consecutive checks.
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

# Default command — overridden per-service in docker-compose.yml
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]