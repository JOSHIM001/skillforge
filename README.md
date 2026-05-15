<div align="center">

# ⚡ SkillForge

### AI-Powered Adaptive Skill Assessment & Career Platform

*Stop self-reporting your skills. Let AI interview you instead.*

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.111-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-336791?style=for-the-badge&logo=postgresql&logoColor=white)](https://postgresql.org)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?style=for-the-badge&logo=redis&logoColor=white)](https://redis.io)
[![Celery](https://img.shields.io/badge/Celery-5.3-37814A?style=for-the-badge&logo=celery&logoColor=white)](https://docs.celeryq.dev)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://docker.com)

[![CI](https://github.com/YOUR_USERNAME/skillforge/actions/workflows/ci.yml/badge.svg)](https://github.com/YOUR_USERNAME/skillforge/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)

</div>

---

## 🧠 What Is SkillForge?

SkillForge is a backend system that conducts **AI-driven adaptive interviews** to objectively measure a developer's skills. Instead of a static quiz, it:

- **Adapts difficulty in real-time** — if you answer well, the next question gets harder
- **Tracks skill growth over time** using spaced repetition (re-assesses skills every 30 days)
- **Runs AI calls asynchronously** via Celery so users never wait on a slow API
- **Syncs live across team members** using WebSockets + Redis pub/sub
- **Analyzes your GitHub profile** to auto-detect and pre-fill your skill matrix

---

## 🏗️ Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Frontend  (Alpine.js + Tailwind + Chart.js)                 │
└────────────────────────┬─────────────────────────────────────┘
                         │ HTTP / WebSocket
┌────────────────────────▼─────────────────────────────────────┐
│  FastAPI  (4 workers in prod, uvicorn)                       │
│  /auth  /skills  /projects  /github  /rooms  /ws/room/{id}  │
├──────────────┬──────────────────┬────────────────────────────┤
│  Middleware  │  Rate Limiter    │  Prompt Injection Guard    │
│  JWT Auth    │  (slowapi+Redis) │  Request ID Tracing        │
└──────┬───────┴──────────────────┴────────────────────────────┘
       │
┌──────▼───────┐    ┌────────────┐    ┌─────────────────────┐
│  PostgreSQL  │    │   Redis    │    │  Groq API           │
│  (primary)   │    │ DB0: cache │    │  llama-3.3-70b      │
│  Users       │    │ DB0: pub/  │    │  ↓ fallback         │
│  Skills      │    │      sub   │    │  OpenAI gpt-4o-mini │
│  Rooms       │    │ DB1: Celery│    └─────────────────────┘
│  Bounties    │    │     broker │
└──────────────┘    │ DB2: task  │
                    │     results│    ┌─────────────────────┐
                    └────────────┘    │  Celery Worker      │
                                      │  generate_question  │
                                      │  grade_answer       │
                                      │  analyze_project    │
                                      ├─────────────────────┤
                                      │  Celery Beat        │
                                      │  spaced repetition  │
                                      │  (daily cron)       │
                                      └─────────────────────┘
```

---

## ✨ Features

| Feature | Details |
|---|---|
| 🎯 **Adaptive Assessment** | Difficulty auto-adjusts per answer (1–5 scale, EMA scoring) |
| 🔄 **Spaced Repetition** | Daily Celery Beat job re-queues skills due for review |
| 🤖 **AI Fallback Chain** | Groq → OpenAI → hardcoded fallback, never crashes |
| ⚡ **Async AI Tasks** | Celery workers handle slow AI calls, returns task_id immediately |
| 🔴 **Real-time Rooms** | Redis pub/sub WebSocket fan-out across multiple server workers |
| 🔐 **Rotating JWT** | 15-min access token + 7-day refresh with replay attack detection |
| 🛡️ **Prompt Injection Guard** | Sanitizes user input before it touches any AI prompt |
| 📊 **Team Skill Matrix** | Visualize collective team skill gaps in a room |
| 🏆 **Skill Bounties** | Post/claim bounties based on verified skill scores |
| 🐙 **GitHub Analysis** | Auto-detect skills from public repos + help-wanted issues |

---

## 🚀 Quick Start (Docker)

### Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- A **Groq API key** — free at [console.groq.com](https://console.groq.com) (takes 30 seconds)

### Step 1 — Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/skillforge.git
cd skillforge
```

### Step 2 — Set up environment

```bash
cp backend/.env.example .env
```

Now open `.env` and fill in your values. The only **required** field is `GROQ_API_KEY`:

```env
GROQ_API_KEY=gsk_your_key_here         # ← get from console.groq.com
SECRET_KEY=your_64_char_random_string  # ← run: openssl rand -hex 32
```

> **Generate a SECRET_KEY:**
> ```bash
> openssl rand -hex 32
> ```

### Step 3 — Start everything

```bash
chmod +x scripts/start.sh
./scripts/start.sh
```

This starts PostgreSQL, Redis, runs Alembic migrations, then starts the API + Celery worker + Celery Beat.

| Service | URL |
|---|---|
| 🌐 App | http://localhost:8000 |
| 📖 API Docs | http://localhost:8000/docs |
| ❤️ Health | http://localhost:8000/health |

### Step 4 — (Optional) Celery monitoring

```bash
docker compose --profile monitoring up -d
```

Flower dashboard → http://localhost:5555 (admin/admin)

---

## 🧑‍💻 Local Dev (without Docker)

> Requires Python 3.11+, PostgreSQL, and Redis running locally.

```bash
# 1. Create virtualenv
cd backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set up your .env (copy from example, fill in DB/Redis/API keys)
cp .env.example ../.env

# 4. Run migrations
alembic upgrade head

# 5. Start API server
uvicorn main:app --reload

# 6. In a second terminal — start Celery worker
celery -A tasks.celery_app.celery_app worker --loglevel=info

# 7. In a third terminal — start Celery Beat (scheduled tasks)
celery -A tasks.celery_app.celery_app beat --loglevel=info
```

---

## 🧪 Running Tests

```bash
chmod +x scripts/test.sh
./scripts/test.sh
```

With coverage report:

```bash
cd backend
pytest tests/ --cov=. --cov-report=html
open htmlcov/index.html   # macOS
```

Test suite covers: auth flows, skill sessions, AI service (mocked), rooms + WebSockets.

---

## 📁 Project Structure

```
skillforge/
├── backend/
│   ├── main.py                      # App factory, middleware, lifespan hooks
│   ├── config.py                    # Pydantic Settings — single source of truth
│   ├── database.py                  # Async SQLAlchemy engine + get_db() dep
│   ├── redis_client.py              # Redis singleton + cache helpers + pub/sub
│   │
│   ├── models/                      # SQLAlchemy ORM (maps to DB tables)
│   │   ├── user.py                  # User, RefreshToken
│   │   ├── skill.py                 # SkillProfile, AssessmentSession, QuestionHistory
│   │   ├── room.py                  # CollabRoom, RoomMember
│   │   └── bounty.py                # Bounty, BountyClaim
│   │
│   ├── schemas/                     # Pydantic (validates API request/response JSON)
│   │   ├── auth.py                  # LoginRequest, TokenPair, UserOut
│   │   ├── skill.py                 # SessionOut, QuestionOut, AnswerRequest
│   │   ├── room.py                  # RoomOut, RoomStateOut
│   │   └── project.py               # ProjectAnalysisOut
│   │
│   ├── routers/                     # HTTP endpoint handlers (thin — no business logic)
│   │   ├── auth.py                  # /auth/* — register, login, refresh, GitHub OAuth
│   │   ├── skills.py                # /skills/* — matrix, sessions, questions, answers
│   │   ├── rooms.py                 # /rooms/* — create, join, leave, state
│   │   ├── bounties.py              # /bounties/* — post, claim, leaderboard
│   │   ├── projects.py              # /projects/* — validate stack, AI blueprint
│   │   ├── github.py                # /github/* — profile, repo analysis, issues
│   │   ├── career_gps.py            # /career-gps — AI career path analysis
│   │   └── admin.py                 # /admin/* — user management (admin only)
│   │
│   ├── services/                    # Business logic (all DB queries live here)
│   │   ├── ai_service.py            # Groq→OpenAI fallback chain + Redis caching
│   │   ├── auth_service.py          # JWT, bcrypt, refresh tokens, GitHub OAuth
│   │   ├── skill_service.py         # Adaptive algorithm, scoring, spaced repetition
│   │   ├── room_service.py          # Team matrix, gap analysis
│   │   ├── project_service.py       # Stack validator
│   │   ├── github_service.py        # GitHub API client
│   │   └── career_gps_service.py    # Career path AI logic
│   │
│   ├── middleware/
│   │   ├── auth_middleware.py       # get_current_user dep + CurrentUser type alias
│   │   ├── security.py              # Request ID, prompt injection, AI rate limits
│   │   └── rate_limiter.py          # slowapi global rate limiter
│   │
│   ├── tasks/
│   │   ├── celery_app.py            # Celery config + Beat schedule
│   │   ├── ai_tasks.py              # generate_question, grade_answer, analyze_project
│   │   └── scheduled_tasks.py       # Daily spaced repetition notifier
│   │
│   ├── websocket/
│   │   ├── manager.py               # RoomManager — Redis pub/sub WebSocket fan-out
│   │   └── ws_router.py             # /ws/room/{id} endpoint
│   │
│   ├── alembic/                     # Database migrations
│   │   └── versions/
│   │       ├── 0001_initial_schema.py
│   │       ├── 0002_bounties.py
│   │       └── 0003_admin_fields.py
│   │
│   ├── tests/
│   │   ├── conftest.py              # Async fixtures, mock AI, SQLite test DB
│   │   ├── test_auth.py
│   │   ├── test_skills.py
│   │   ├── test_ai_service.py
│   │   └── test_rooms.py
│   │
│   ├── requirements.txt
│   └── .env.example                 # ← copy this to ../.env and fill in values
│
├── frontend/
│   ├── index.html                   # Alpine.js + Tailwind CSS + Chart.js
│   └── admin.html
│
├── scripts/
│   ├── start.sh                     # Dev startup (migrations + all services)
│   └── test.sh                      # Test runner
│
├── .github/
│   └── workflows/
│       ├── ci.yml                   # Lint + test on every PR
│       └── deploy.yml               # Build + push + deploy on merge to main
│
├── Dockerfile                       # Multi-stage production build
├── docker-compose.yml               # Local dev stack
├── docker-compose.prod.yml          # Production overrides
├── pyproject.toml                   # pytest + ruff + mypy config
└── .env.example                     # Root-level example (copy to .env)
```

---

## 🔌 API Reference

Full interactive docs → **http://localhost:8000/docs**

<details>
<summary><b>Auth Endpoints</b></summary>

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/auth/register` | ❌ | Register with email + password |
| `POST` | `/auth/login` | ❌ | Login → returns JWT + sets refresh cookie |
| `POST` | `/auth/refresh` | Cookie | Rotate refresh token → new access token |
| `POST` | `/auth/logout` | Cookie | Revoke refresh token |
| `GET` | `/auth/me` | JWT | Current user info |
| `GET` | `/auth/github/login` | ❌ | Redirect to GitHub OAuth |
| `GET` | `/auth/github/callback` | ❌ | GitHub OAuth callback |

</details>

<details>
<summary><b>Skill Assessment Endpoints</b></summary>

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/skills/matrix` | JWT | Full skill radar |
| `GET` | `/skills/matrix/spaced-queue` | JWT | Skills due for review |
| `POST` | `/skills/session/start` | JWT | Start adaptive session |
| `GET` | `/skills/session/{id}/question` | JWT | Get next AI question |
| `POST` | `/skills/session/{id}/answer` | JWT | Submit answer → 202 + task_id |
| `POST` | `/skills/session/{id}/complete` | JWT | End session + update scores |
| `GET` | `/skills/tasks/{task_id}` | JWT | Poll async Celery task result |

</details>

<details>
<summary><b>Rooms (Collaboration) Endpoints</b></summary>

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/rooms` | JWT | Create room |
| `GET` | `/rooms/{id}` | JWT | Room state + team skill matrix |
| `POST` | `/rooms/{id}/join` | JWT | Join room |
| `POST` | `/rooms/{id}/leave` | JWT | Leave room |
| `WS` | `/ws/room/{id}?token=` | JWT query | Live collaboration WebSocket |

</details>

<details>
<summary><b>WebSocket Protocol</b></summary>

```js
// Connect — JWT goes in query string (browsers can't set WS headers)
const ws = new WebSocket(`ws://localhost:8000/ws/room/${roomId}?token=${jwt}`);

// Messages you RECEIVE from server
ws.onmessage = ({ data }) => {
  const msg = JSON.parse(data);
  switch (msg.type) {
    case "room_update": // full room state in msg.payload
    case "join":        // { user_id, username }
    case "leave":       // { user_id, username }
    case "chat":        // { user_id, username, text }
    case "pong":        // response to ping
    case "error":       // { detail }
  }
};

// Messages you SEND to server
ws.send(JSON.stringify({ type: "ping" }));
ws.send(JSON.stringify({ type: "chat", text: "Hello team!" }));
ws.send(JSON.stringify({ type: "request_state" }));
```

</details>

---

## ⚙️ Environment Variables

Copy `backend/.env.example` to `.env` in the root folder.

| Variable | Required | How to get it |
|---|---|---|
| `DATABASE_URL` | ✅ | `postgresql+asyncpg://user:password@postgres:5432/skillapp` |
| `REDIS_URL` | ✅ | `redis://redis:6379/0` |
| `CELERY_BROKER_URL` | ✅ | `redis://redis:6379/1` |
| `CELERY_RESULT_BACKEND` | ✅ | `redis://redis:6379/2` |
| `SECRET_KEY` | ✅ | Run `openssl rand -hex 32` |
| `GROQ_API_KEY` | ✅ | [console.groq.com](https://console.groq.com) — free |
| `GITHUB_CLIENT_ID` | ⬜ | [GitHub → Settings → Developer Settings → OAuth Apps](https://github.com/settings/developers) |
| `GITHUB_CLIENT_SECRET` | ⬜ | Same as above |
| `OPENAI_API_KEY` | ⬜ | [platform.openai.com](https://platform.openai.com) — fallback AI only |
| `SENTRY_DSN` | ⬜ | [sentry.io](https://sentry.io) — error tracking |
| `ENVIRONMENT` | ⬜ | `development` or `production` |
| `FRONTEND_URL` | ⬜ | `http://localhost:8000` in dev |

> ⚠️ **Never commit your `.env` file.** It is in `.gitignore`. Use GitHub Secrets for CI/CD.

---

## 🚢 Deployment

### GitHub Actions Secrets (one-time setup)

Go to your repo → **Settings → Secrets and variables → Actions** → add:

| Secret Name | Value |
|---|---|
| `DATABASE_URL` | Your production PostgreSQL URL |
| `GROQ_API_KEY` | Your Groq API key |
| `SECRET_KEY` | A new random 64-char string for production |
| `GITHUB_CLIENT_ID` | Your GitHub OAuth app client ID |
| `GITHUB_CLIENT_SECRET` | Your GitHub OAuth app client secret |
| `DEPLOY_HOST` | Your server IP or hostname |
| `DEPLOY_USER` | SSH username (e.g. `ubuntu`) |
| `DEPLOY_SSH_KEY` | Your private SSH key (the entire file content) |

### Deploy

```bash
git push origin main   # CI runs tests → deploys automatically if tests pass
```

### Manual deploy

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide. Short version:

1. Pick an issue or create one
2. `git checkout -b feat/your-feature-name`
3. Make changes, write tests
4. `git push origin feat/your-feature-name`
5. Open a Pull Request — CI must pass before merge

---

## 📄 License

MIT — see [LICENSE](LICENSE) for details.
