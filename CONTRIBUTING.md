# Contributing to SkillForge

Welcome to the team! This guide covers everything you need to contribute effectively — from setting up your environment to getting a PR merged.

---

## 🧭 Table of Contents

- [Team Setup (Read First)](#team-setup-read-first)
- [Branch Strategy](#branch-strategy)
- [How to Pick Up a Task](#how-to-pick-up-a-task)
- [Development Workflow](#development-workflow)
- [Commit Message Format](#commit-message-format)
- [Pull Request Rules](#pull-request-rules)
- [Code Standards](#code-standards)
- [Testing Requirements](#testing-requirements)
- [What NOT to Do](#what-not-to-do)

---

## 🛠️ Team Setup (Read First)

### 1. Get access

Ask the project owner to add you as a **Collaborator**:
> GitHub repo → Settings → Collaborators → Add people → enter your GitHub username

### 2. Clone the repo

```bash
git clone https://github.com/YOUR_ORG/skillforge.git
cd skillforge
```

### 3. Set up your own `.env`

```bash
cp backend/.env.example .env
```

Open `.env` and fill in:
- `GROQ_API_KEY` → Get your own free key at [console.groq.com](https://console.groq.com) (takes 30 seconds, free tier is enough)
- `SECRET_KEY` → Run `openssl rand -hex 32` and paste the result
- Leave `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` blank unless you need OAuth testing

> ⚠️ **Every team member uses their OWN API keys in their local `.env`.** Never share keys. Never commit `.env`.

### 4. Start the project

```bash
./scripts/start.sh
```

---

## 🌿 Branch Strategy

We use **GitHub Flow** — simple and effective for a team of 4.

```
main ──────────────────────────────────────────────────────►
  │                                                         │
  ├── feat/adaptive-scoring ──────────── PR ──────────────►│
  │                                                         │
  ├── fix/refresh-token-expiry ─────── PR ────────────────►│
  │                                                         │
  └── chore/update-dependencies ───── PR ─────────────────►│
```

### Branch naming rules

| Type | Format | Example |
|---|---|---|
| New feature | `feat/short-description` | `feat/skill-bounty-claim` |
| Bug fix | `fix/short-description` | `fix/websocket-disconnect-leak` |
| Refactor | `refactor/short-description` | `refactor/ai-service-retry` |
| Docs | `docs/short-description` | `docs/api-reference-update` |
| Chore | `chore/short-description` | `chore/bump-fastapi-version` |

### Rules

- ✅ `main` is **always deployable** — CI passes, code works
- ❌ **Never push directly to `main`** — always use a PR
- ❌ **Never force-push to `main`**
- ✅ Delete your branch after it's merged

---

## 📋 How to Pick Up a Task

1. Go to **Issues** tab on GitHub
2. Find an unassigned issue — look for `good first issue` if you're new
3. **Comment on the issue** saying you're picking it up (prevents duplicate work)
4. Create your branch:

```bash
git checkout main
git pull origin main                      # always start from latest main
git checkout -b feat/your-feature-name
```

---

## 💻 Development Workflow

```bash
# 1. Pull latest changes before starting any work
git checkout main
git pull origin main

# 2. Create your feature branch
git checkout -b feat/your-feature

# 3. Make your changes (see Code Standards below)

# 4. Run tests before committing
cd backend
pytest tests/ -v

# 5. Stage and commit
git add .
git commit -m "feat: add bounty claim validation"

# 6. Push your branch
git push origin feat/your-feature

# 7. Open a Pull Request on GitHub
# → Go to repo → "Compare & pull request" button
```

### Keeping your branch up to date

If `main` moves forward while you're working:

```bash
git fetch origin
git rebase origin/main        # preferred over merge — keeps history clean
```

---

## 📝 Commit Message Format

We follow **Conventional Commits**. This makes the git log readable and enables automatic changelog generation.

```
<type>: <short description>

[optional body — explain WHY, not what]
```

### Types

| Type | When to use |
|---|---|
| `feat` | New feature |
| `fix` | Bug fix |
| `refactor` | Code change that doesn't fix a bug or add feature |
| `test` | Adding or fixing tests |
| `docs` | Documentation only |
| `chore` | Build, deps, config changes |
| `perf` | Performance improvement |

### Examples

```bash
# Good
git commit -m "feat: add skill bounty leaderboard endpoint"
git commit -m "fix: refresh token replay detection not revoking all tokens"
git commit -m "refactor: extract AI prompt templates into constants"
git commit -m "test: add WebSocket disconnect cleanup test"
git commit -m "docs: update WebSocket protocol in README"

# Bad — don't do these
git commit -m "fix"
git commit -m "changes"
git commit -m "WIP"
git commit -m "asdfgh"
```

---

## 🔍 Pull Request Rules

### Before opening a PR

- [ ] All tests pass locally: `pytest tests/ -v`
- [ ] No new linting errors: `ruff check backend/`
- [ ] You've pulled latest `main` and rebased
- [ ] Your branch name follows the naming convention
- [ ] You haven't committed `.env` or any secrets

### PR description template

When you open a PR, fill this out:

```markdown
## What does this PR do?
[1-2 sentence summary]

## Why?
[Link to issue: Closes #42]

## How to test it?
[Step by step instructions for reviewer]

## Screenshots (if UI change)
[paste screenshot]

## Checklist
- [ ] Tests pass
- [ ] No secrets committed
- [ ] Code follows project conventions
```

### Review process

- Every PR needs **at least 1 approval** before merging
- The PR author does NOT merge their own PR — the reviewer merges it
- CI must pass (lint + tests) — GitHub blocks merge if CI fails
- If reviewer requests changes: make changes, push to same branch, re-request review

### Who reviews what

Since we're a team of 4, rotate reviewers. Suggested pairing:
- If you work on `services/` → get someone who knows `routers/` to review
- If you change `middleware/` → everyone should review (security-critical)
- If you change `tasks/` → get someone familiar with Celery to review

---

## 🎯 Code Standards

### Layer rules (enforce strictly)

```
Router  → only handles HTTP, calls service, returns response
Service → all business logic, all DB queries
Model   → only table definitions, no logic
Schema  → only data shape validation, no logic
```

**If your router function has a `SELECT` statement in it → move it to the service.**

### Type hints — always

```python
# Good
async def get_user(user_id: uuid.UUID, db: AsyncSession) -> User | None:

# Bad
async def get_user(user_id, db):
```

### Async — always use await properly

```python
# Good
result = await db.execute(select(User).where(User.id == user_id))

# Bad — blocks the event loop
import time
time.sleep(2)  # use asyncio.sleep(2) instead
```

### Error handling — be specific

```python
# Good
except groq.RateLimitError as e:
    logger.warning("Groq rate limited", error=str(e))
    raise

# Bad
except Exception:
    pass  # never swallow exceptions silently
```

### Logging — use loguru with context

```python
from loguru import logger

logger.info("Session started", user_id=str(user_id), skill=skill_name)
logger.warning("AI fallback triggered", error=str(e), attempt=retry_count)
logger.error("DB query failed", table="users", error=str(e))
```

---

## 🧪 Testing Requirements

Every PR that changes logic **must include tests**. No exceptions.

### Where to add tests

| Changed file | Test file |
|---|---|
| `services/auth_service.py` | `tests/test_auth.py` |
| `services/skill_service.py` | `tests/test_skills.py` |
| `services/ai_service.py` | `tests/test_ai_service.py` |
| `websocket/manager.py` | `tests/test_rooms.py` |
| New service | New `tests/test_<name>.py` |

### Test conventions

```python
# Test names: test_<what>_<condition>_<expected result>
async def test_login_with_wrong_password_returns_401():
    ...

async def test_refresh_token_rotation_revokes_old_token():
    ...

async def test_grade_answer_task_sanitizes_injection_attempt():
    ...
```

### Mocking the AI (always mock in tests)

```python
# conftest.py already has this fixture — use it
@pytest.fixture
def mock_ai(monkeypatch):
    async def fake_ai(*args, **kwargs):
        return {"score": 0.8, "feedback": "Good answer", "key_points": ["point1"]}
    monkeypatch.setattr("services.ai_service.call_ai", fake_ai)
```

---

## 🚫 What NOT to Do

These will get your PR rejected immediately:

| Don't | Because |
|---|---|
| Commit `.env` | Exposes API keys to the entire internet |
| Push to `main` directly | Bypasses review, can break production |
| Add `print()` statements | Use `logger.debug()` instead |
| Write raw SQL strings | Use SQLAlchemy ORM — prevents SQL injection |
| Store passwords in plaintext | Always use `hash_password()` from auth_service |
| Add API keys in code | Always read from `settings.your_key` via config.py |
| Delete migrations | Migrations are the DB change history — never delete |
| Merge your own PR | Another team member must approve and merge |

---

## 🆘 Help

If you're stuck:
1. Check the [README](README.md) first
2. Check if there's a related issue on GitHub
3. Post in the team chat with: **what you tried**, **what error you got**, **what you expected**
4. Pair program — don't sit stuck for more than 30 minutes alone

---

*Happy building! The goal is to ship good code together, not to be perfect alone.*
