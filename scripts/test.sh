#!/usr/bin/env bash
# test.sh — Run the full test suite locally
#
# Usage:
#   ./scripts/test.sh                  # run all tests
#   ./scripts/test.sh -k test_auth     # run specific tests
#   ./scripts/test.sh --cov            # with coverage report

set -euo pipefail

EXTRA_ARGS=("$@")
COMPOSE="docker compose"

echo "🧪 SkillForge test runner"

# ── Ensure test infrastructure is running ─────────────────────
if ! $COMPOSE ps postgres | grep -q "healthy" 2>/dev/null; then
  echo "🐘 Starting PostgreSQL for tests..."
  $COMPOSE up -d postgres redis
  until $COMPOSE exec -T postgres pg_isready -U user -d skillapp &>/dev/null; do
    sleep 1
  done
fi

# ── Write a test .env if not present ──────────────────────────
TEST_ENV=$(cat <<'EOF'
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/skillapp
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/1
CELERY_RESULT_BACKEND=redis://localhost:6379/2
SECRET_KEY=test-only-secret-key-not-for-production-use
GROQ_API_KEY=test_key
OPENAI_API_KEY=test_key
GITHUB_CLIENT_ID=test_id
GITHUB_CLIENT_SECRET=test_secret
ENVIRONMENT=development
EOF
)

# ── Run pytest ────────────────────────────────────────────────
pushd backend > /dev/null

echo "$TEST_ENV" > .env.test

pytest tests/ \
  --asyncio-mode=auto \
  --tb=short \
  -v \
  "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"

popd > /dev/null
echo "✅ All tests passed"