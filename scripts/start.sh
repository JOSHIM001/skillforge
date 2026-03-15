#!/usr/bin/env bash
# start.sh — Start the full SkillForge dev stack
#
# Usage:
#   ./scripts/start.sh          # start all services
#   ./scripts/start.sh --fresh  # wipe volumes and start clean

set -euo pipefail

COMPOSE="docker compose"
FRESH=false

for arg in "$@"; do
  [[ "$arg" == "--fresh" ]] && FRESH=true
done

echo "🔧 SkillForge dev startup"

# ── Check .env exists ─────────────────────────────────────────
if [[ ! -f .env ]]; then
  echo "⚠️  .env not found — copying from .env.example"
  cp backend/.env.example .env
  echo "✏️  Edit .env and add your API keys, then re-run this script."
  exit 1
fi

# ── Wipe volumes if --fresh ───────────────────────────────────
if [[ "$FRESH" == true ]]; then
  echo "🗑️  Wiping volumes (--fresh mode)"
  $COMPOSE down -v --remove-orphans
fi

# ── Start infrastructure first ────────────────────────────────
echo "🐘 Starting PostgreSQL and Redis..."
$COMPOSE up -d postgres redis

echo "⏳ Waiting for postgres to be healthy..."
until $COMPOSE exec -T postgres pg_isready -U user -d skillapp &>/dev/null; do
  sleep 1
done
echo "✅ PostgreSQL ready"

until $COMPOSE exec -T redis redis-cli ping &>/dev/null; do
  sleep 1
done
echo "✅ Redis ready"

# ── Run migrations ────────────────────────────────────────────
#echo "📦 Running Alembic migrations..."
#$COMPOSE run --rm api alembic upgrade head
#echo "✅ Migrations complete"

# ── Start all services ────────────────────────────────────────
echo "🚀 Starting API, worker, and beat..."
$COMPOSE up -d api worker beat

echo ""
echo "═══════════════════════════════════════"
echo "  SkillForge is running!"
echo "  API:    http://localhost:8000"
echo "  Docs:   http://localhost:8000/docs"
echo "  Health: http://localhost:8000/health"
echo ""
echo "  Celery Flower (optional):"
echo "  docker compose --profile monitoring up -d flower"
echo "  UI: http://localhost:5555"
echo "═══════════════════════════════════════"