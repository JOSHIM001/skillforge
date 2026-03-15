# SkillForge — AI-Driven Skill Assessment & Career Platform

> Eliminate self-reporting bias. Let AI interview you instead.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Frontend (index.html)                              │
│  Alpine.js · Tailwind CSS · Chart.js · Lucide       │
└────────────────────┬────────────────────────────────┘
                     │ HTTP / WebSocket
┌────────────────────▼────────────────────────────────┐
│  FastAPI API  (uvicorn, 4 workers in prod)          │
│  /auth  /skills  /projects  /github  /rooms         │
│  /ws/room/{id}  — WebSocket                         │
└──────┬─────────────┬───────────────┬────────────────┘
       │             │               │
  ┌────▼────┐  ┌─────▼─────┐  ┌─────▼──────┐
  │Postgres │  │   Redis    │  │  Groq API  │
  │(models) │  │(cache+pub) │  │ (+ OAI fb) │
  └─────────┘  └─────┬─────┘  └────────────┘
                     │
              ┌──────▼──────┐
              │Celery Worker│  ← AI tasks > 3s
              │Celery Beat  │  ← Spaced repetition
              └─────────────┘
```

## Quick Start

### Prerequisites
- Docker + Docker Compose
- A [Groq API key](https://console.groq.com) (free tier)
- A [GitHub OAuth App](https://github.com/settings/developers) (optional)

### 1. Clone and configure

```bash
git clone https://github.com/yourname/skillforge
cd skillforge
cp backend/.env.example .env
# Edit .env — add GROQ_API_KEY at minimum
```

### 2. Start everything

```bash
chmod +x scripts/start.sh
./scripts/start.sh
```

That's it. The script:
1. Starts PostgreSQL + Redis
2. Runs Alembic migrations
3. Starts API + Celery worker + Celery Beat

- **API:**  http://localhost:8000
- **Docs:** http://localhost:8000/docs
- **Health:** http://localhost:8000/health

### 3. Run tests

```bash
chmod +x scripts/test.sh
./scripts/test.sh
```

With coverage:
```bash
cd backend && pytest tests/ --cov=. --cov-report=html
open htmlcov/index.html
```

## Project Structure

```
skillforge/
├── backend/
│   ├── main.py                  # FastAPI app factory
│   ├── config.py                # Pydantic settings (.env)
│   ├── database.py              # Async SQLAlchemy engine
│   ├── redis_client.py          # Redis singleton + helpers
│   │
│   ├── models/
│   │   ├── user.py              # User, RefreshToken
│   │   ├── skill.py             # SkillProfile, AssessmentSession, QuestionHistory
│   │   └── room.py              # CollabRoom, RoomMember
│   │
│   ├── schemas/
│   │   ├── auth.py              # Auth request/response schemas
│   │   ├── skill.py             # Skill assessment schemas
│   │   └── room.py              # Room + WebSocket message schemas
│   │
│   ├── routers/
│   │   ├── auth.py              # /auth/* — register, login, GitHub OAuth
│   │   ├── skills.py            # /skills/* — matrix, sessions, questions
│   │   ├── projects.py          # /projects/* — validator, blueprint
│   │   ├── github.py            # /github/* — help-wanted issues
│   │   └── rooms.py             # /rooms/* — create, join, leave
│   │
│   ├── services/
│   │   ├── ai_service.py        # Groq + OpenAI fallback + Redis cache
│   │   ├── auth_service.py      # JWT, refresh tokens, GitHub OAuth
│   │   ├── skill_service.py     # Adaptive algorithm, spaced repetition
│   │   ├── project_service.py   # Stack validator + generic filter
│   │   └── room_service.py      # Team matrix, role gap analysis
│   │
│   ├── websocket/
│   │   ├── manager.py           # RoomManager (Redis pub/sub)
│   │   └── ws_router.py         # /ws/room/{id} endpoint
│   │
│   ├── middleware/
│   │   ├── auth_middleware.py   # JWT dependency + CurrentUser type alias
│   │   └── rate_limiter.py      # slowapi limiter
│   │
│   ├── tasks/
│   │   ├── celery_app.py        # Celery instance + Beat schedule
│   │   ├── ai_tasks.py          # generate_question, grade_answer tasks
│   │   └── scheduled_tasks.py  # Daily spaced repetition notifier
│   │
│   └── tests/
│       ├── conftest.py          # Async fixtures, mock AI, test DB
│       ├── test_auth.py         # 15 auth tests
│       ├── test_skills.py       # 18 skill tests
│       ├── test_ai_service.py   # 16 AI service tests
│       └── test_rooms.py        # 18 room + WebSocket tests
│
├── alembic/
│   ├── env.py                   # Async-compatible Alembic env
│   └── versions/
│       └── 0001_initial_schema.py
│
├── frontend/
│   └── index.html               # Alpine.js + Tailwind + Chart.js
│
├── scripts/
│   ├── start.sh                 # Dev startup script
│   └── test.sh                  # Test runner script
│
├── .github/workflows/
│   ├── ci.yml                   # Lint + test on every PR
│   └── deploy.yml               # Build + push + deploy on merge to main
│
├── Dockerfile                   # Multi-stage production build
├── docker-compose.yml           # Local dev stack
├── docker-compose.prod.yml      # Production overrides
├── pyproject.toml               # pytest + ruff + mypy config
└── alembic.ini
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | ✅ | `postgresql+asyncpg://...` |
| `REDIS_URL` | ✅ | `redis://localhost:6379/0` |
| `SECRET_KEY` | ✅ | 64-char random string for JWT signing |
| `GROQ_API_KEY` | ✅ | From console.groq.com |
| `OPENAI_API_KEY` | ⬜ | Optional fallback |
| `GITHUB_CLIENT_ID` | ⬜ | For GitHub OAuth login |
| `GITHUB_CLIENT_SECRET` | ⬜ | For GitHub OAuth login |
| `SENTRY_DSN` | ⬜ | Error tracking |
| `ENVIRONMENT` | ⬜ | `development` or `production` |

## API Reference

Full interactive docs at `http://localhost:8000/docs`

### Auth
| Method | Path | Description |
|---|---|---|
| `POST` | `/auth/register` | Email/password registration |
| `POST` | `/auth/login` | Email/password login |
| `POST` | `/auth/refresh` | Rotate refresh token |
| `POST` | `/auth/logout` | Revoke refresh token |
| `GET`  | `/auth/me` | Current user |
| `GET`  | `/auth/github/login` | GitHub OAuth redirect |
| `GET`  | `/auth/github/callback` | GitHub OAuth callback |

### Skills
| Method | Path | Description |
|---|---|---|
| `GET`  | `/skills/matrix` | Full skill profile |
| `GET`  | `/skills/matrix/spaced-queue` | Skills due for review |
| `POST` | `/skills/session/start` | Start adaptive session |
| `GET`  | `/skills/session/{id}/question` | Get next question |
| `POST` | `/skills/session/{id}/answer` | Submit + grade answer |
| `POST` | `/skills/session/{id}/complete` | End session |
| `GET`  | `/skills/tasks/{task_id}` | Poll async task |

### Rooms
| Method | Path | Description |
|---|---|---|
| `POST` | `/rooms` | Create room |
| `GET`  | `/rooms/{id}` | Room state + team matrix |
| `POST` | `/rooms/{id}/join` | Join room |
| `POST` | `/rooms/{id}/leave` | Leave room |
| `WS`   | `/ws/room/{id}?token=` | Live collaboration |

## WebSocket Protocol

```js
// Connect
const ws = new WebSocket(`ws://localhost:8000/ws/room/${roomId}?token=${jwt}`);

// Incoming message types
ws.onmessage = ({ data }) => {
  const msg = JSON.parse(data);
  switch (msg.type) {
    case "room_update": // full RoomStateOut in msg.payload
    case "join":        // msg.user_id, msg.username
    case "leave":       // msg.user_id, msg.username
    case "chat":        // msg.user_id, msg.username, msg.text
    case "pong":        // response to ping
    case "error":       // msg.detail
  }
};

// Outgoing message types
ws.send(JSON.stringify({ type: "ping" }));
ws.send(JSON.stringify({ type: "chat", text: "Hello team!" }));
ws.send(JSON.stringify({ type: "request_state" }));
```

## GitHub Actions Secrets

For the deploy workflow, configure these in **Settings → Secrets → Actions**:

| Secret | Description |
|---|---|
| `DATABASE_URL` | Production DB connection string |
| `DEPLOY_HOST` | SSH target hostname |
| `DEPLOY_USER` | SSH username |
| `DEPLOY_SSH_KEY` | Private SSH key |

## Production Deployment

```bash
# Build + push + migrate + deploy
git push origin main    # triggers .github/workflows/deploy.yml

# Manual deploy
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Check health
curl https://your-domain/health
```