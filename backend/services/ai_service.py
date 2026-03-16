"""
ai_service.py — Central AI caller.

All AI calls in the application go through call_ai(). Never call Groq or
OpenAI directly from a router or service — always go through this module.

Architecture:
  1. Check Redis cache (cache_ttl=0 skips this)
  2. Try Groq (llama-3.3-70b-versatile) with up to 2 retries on transient errors
  3. On Groq failure → fall back to OpenAI (gpt-4o-mini)
  4. On both failures → return {} and let the caller use its hardcoded fallback
  5. Cache successful responses in Redis

Every prompt MUST end with a JSON schema instruction.
Every caller MUST have a hardcoded fallback dict for when this returns {}.
"""

import hashlib
import json
import logging
from typing import Any

from groq import AsyncGroq, APIConnectionError, APIStatusError, RateLimitError
from openai import AsyncOpenAI, APIError as OAIAPIError
from loguru import logger

from config import settings
from redis_client import cache_get, cache_set

# ── SDK clients (module-level singletons) ─────────────────────────────────────
_groq_client: AsyncGroq | None = None
_oai_client: AsyncOpenAI | None = None


def _get_groq() -> AsyncGroq:
    global _groq_client
    if _groq_client is None:
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is not set in environment")
        _groq_client = AsyncGroq(api_key=settings.groq_api_key)
    return _groq_client


def _get_oai() -> AsyncOpenAI | None:
    global _oai_client
    if not settings.openai_api_key:
        return None
    if _oai_client is None:
        _oai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _oai_client


# ── Cache key ─────────────────────────────────────────────────────────────────
def _cache_key(system: str, prompt: str) -> str:
    """Deterministic Redis key from the full prompt content."""
    digest = hashlib.md5((system + "||" + prompt).encode()).hexdigest()
    return f"ai:response:{digest}"


# ── JSON extractor ────────────────────────────────────────────────────────────
def _extract_json(raw: str) -> dict:
    """
    Parse JSON from an AI response, handling common formatting issues:
    - Markdown code fences (```json ... ```)
    - Leading/trailing whitespace
    - Single-key wrappers that some models add
    """
    text = raw.strip()

    # Strip markdown fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Last resort: find the first { ... } block in the response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start != -1 and end > start:
            return json.loads(text[start:end])
        raise


# ── Groq call ─────────────────────────────────────────────────────────────────
async def _call_groq(system: str, prompt: str, max_tokens: int) -> dict:
    """
    Call Groq with llama-3.3-70b-versatile.
    Retries once on RateLimitError (with a short wait) and once on
    transient connection errors. Raises on all other failures.
    """
    import asyncio

    client = _get_groq()
    last_exc: Exception | None = None

    for attempt in range(2):
        try:
            resp = await client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user",   "content": prompt},
                ],
                temperature=0.2,        # Low temperature = more consistent JSON
                max_tokens=max_tokens,
            )
            raw = resp.choices[0].message.content or ""
            return _extract_json(raw)

        except RateLimitError as exc:
            last_exc = exc
            if attempt == 0:
                logger.warning("Groq rate limit hit, retrying in 2s")
                await asyncio.sleep(2)
            continue

        except APIConnectionError as exc:
            last_exc = exc
            if attempt == 0:
                await asyncio.sleep(1)
            continue

        except APIStatusError as exc:
            # 4xx errors (bad request, auth failure) — don't retry
            logger.error("Groq API status error", status_code=exc.status_code, body=exc.body)
            raise

        except json.JSONDecodeError as exc:
            logger.warning("Groq returned non-JSON response", error=str(exc))
            raise

    raise last_exc or RuntimeError("Groq call failed after retries")


# ── OpenAI fallback ───────────────────────────────────────────────────────────
async def _call_openai(system: str, prompt: str, max_tokens: int) -> dict:
    """
    Fallback to OpenAI gpt-4o-mini when Groq is unavailable.
    Returns {} if OPENAI_API_KEY is not configured.
    """
    client = _get_oai()
    if client is None:
        logger.warning("OpenAI fallback skipped — OPENAI_API_KEY not configured")
        return {}

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        raw = resp.choices[0].message.content or ""
        return _extract_json(raw)

    except OAIAPIError as exc:
        logger.error("OpenAI fallback also failed", error=str(exc))
        return {}

    except json.JSONDecodeError as exc:
        logger.warning("OpenAI returned non-JSON response", error=str(exc))
        return {}


# ── Public interface ──────────────────────────────────────────────────────────
async def call_ai(
    prompt: str,
    system: str,
    cache_ttl: int = 3600,
    max_tokens: int = 1000,
) -> dict:
    """
    The single entry point for all AI calls in the application.

    Args:
        prompt:    The user-turn prompt. Should end with the expected JSON schema.
        system:    The system prompt defining the AI's role and output format.
        cache_ttl: Seconds to cache the response in Redis.
                   Pass 0 to disable caching (use for question generation,
                   grading — any call where identical inputs should vary).
        max_tokens: Upper bound on response length.

    Returns:
        Parsed JSON dict on success.
        Empty dict {} on total failure — callers MUST handle this.

    Example:
        result = await call_ai(prompt, system, cache_ttl=0)
        return result or HARDCODED_FALLBACK
    """
    key = _cache_key(system, prompt)

    # ── 1. Cache check ────────────────────────────────────────
    if cache_ttl > 0:
        cached = await cache_get(key)
        if cached is not None:
            logger.debug("AI cache hit", key=key)
            return cached if isinstance(cached, dict) else {}

    # ── 2. Try Groq ───────────────────────────────────────────
    result: dict = {}
    try:
        result = await _call_groq(system, prompt, max_tokens)
        logger.debug("Groq call succeeded")
    except Exception as groq_exc:
        logger.warning("Groq failed, trying OpenAI fallback", error=str(groq_exc))

        # ── 3. Fallback to OpenAI ─────────────────────────────
        result = await _call_openai(system, prompt, max_tokens)

    # ── 4. Cache successful response ──────────────────────────
    if result and cache_ttl > 0:
        await cache_set(key, result, ttl=cache_ttl)

    return result  # {} if both providers failed


# ── Typed AI call helpers ─────────────────────────────────────────────────────
# Higher-level wrappers used by skill_service, project_service, room_service.
# Each defines its own system prompt + hardcoded fallback.

async def generate_question(
    skill: str,
    difficulty: int,
    previous_questions: list[str],
) -> dict:
    """
    Generate a single adaptive technical interview question.

    Returns:
        {
          "question": "...",
          "expected_topics": ["topic1", "topic2"],
          "difficulty": 3
        }
    """
    DIFFICULTY_MAP = {
        1: "basic conceptual",
        2: "beginner practical",
        3: "intermediate",
        4: "advanced",
        5: "expert / system design level",
    }
    level_label = DIFFICULTY_MAP.get(difficulty, "intermediate")

    system = (
        "You are a senior technical interviewer conducting a live coding/knowledge assessment. "
        "Generate exactly ONE interview question. "
        "Return ONLY valid JSON matching this schema exactly: "
        '{"question": "string", "expected_topics": ["string"], "difficulty": integer}'
    )

    # Only pass last 3 questions to keep prompt short
    recent = previous_questions[-3:] if previous_questions else []
    prev_block = f"Previously asked (do NOT repeat these topics): {recent}" if recent else "No previous questions."

    prompt = (
        f"Skill being assessed: {skill}\n"
        f"Required difficulty level: {level_label} (level {difficulty}/5)\n"
        f"{prev_block}\n\n"
        "Generate a question that:\n"
        "- Tests practical understanding, not trivia\n"
        "- Is answerable in 3-5 sentences by someone at this level\n"
        "- Covers a different topic than the previous questions\n"
        "Return ONLY valid JSON."
    )

    result = await call_ai(prompt, system, cache_ttl=0)  # never cache questions

    return result or {
        "question": f"Explain a key concept in {skill} and describe a real-world situation where you applied it.",
        "expected_topics": [skill, "practical application"],
        "difficulty": difficulty,
    }


async def grade_answer(
    question: str,
    user_answer: str,
    expected_topics: list[str],
    skill: str,
) -> dict:
    """
    Grade a user's answer against the question and expected topics.

    Returns:
        {
          "score": 0.75,          # 0.0 wrong → 1.0 excellent
          "feedback": "...",
          "topics_covered": ["topic1"]
        }
    """
    system = (
        "You are a senior software engineer grading a technical interview answer. "
        "Be fair but rigorous. Partial credit is valid. "
        "Return ONLY valid JSON matching this schema exactly: "
        '{"score": float, "feedback": "string", "topics_covered": ["string"]}'
        "\nScore guide: 0.0=completely wrong, 0.3=shows awareness, "
        "0.5=partially correct, 0.75=good with minor gaps, 1.0=excellent and complete."
    )

    prompt = (
        f"Skill: {skill}\n"
        f"Question: {question}\n"
        f"Expected topics to cover: {expected_topics}\n"
        f"Candidate's answer: {user_answer}\n\n"
        "Evaluate:\n"
        "1. Correctness of the core answer\n"
        "2. Which expected topics were mentioned\n"
        "3. Depth and clarity of explanation\n"
        "Provide actionable feedback in 1-2 sentences.\n"
        "Return ONLY valid JSON."
    )

    result = await call_ai(prompt, system, cache_ttl=0)  # never cache grades

    return result or {
        "score": 0.5,
        "feedback": "Could not evaluate automatically. Score set to neutral.",
        "topics_covered": [],
    }


async def validate_project_stack(
    project_idea: str,
    current_stack: list[str],
) -> dict:
    """
    Identify specific missing technologies for a project idea.

    Returns:
        {
          "missing_tech": ["Redis", "OAuth2", "WebSockets"],
          "reasoning": "..."
        }
    """
    system = (
        "You are a Senior Software Architect reviewing a project proposal. "
        "Identify SPECIFIC missing technologies — name actual libraries and tools, not concepts. "
        "Be precise: say 'Celery' not 'task queue', 'JWT' not 'authentication'. "
        "Return ONLY valid JSON matching this schema: "
        '{"missing_tech": ["string"], "reasoning": "string", '
        '"market_relevance": {"score": 85, "label": "High Demand", "summary": "2 sentence plain English summary of market demand for this project type", '
        '"top_industries": ["EdTech", "Enterprise"], "similar_products": ["Google Maps", "Wayfair"], '
        '"hiring_demand": "High — 12,000+ job postings need these skills", '
        '"verdict": "one sentence telling the developer if this is worth building for their career"}}'
    )

    prompt = (
        f"Project idea: {project_idea}\n"
        f"Current tech stack: {current_stack or ['none specified']}\n\n"
        "1. List the specific technologies this project needs but doesn't have yet.\n"
        "2. Explain in plain English (not jargon) what is missing and why it matters.\n"
        "3. Analyze the MARKET RELEVANCE of this project:\n"
        "   - Score 0-100 based on current industry demand\n"
        "   - Which industries need this type of project\n"
        "   - Similar successful products that exist\n"
        "   - How many jobs require building things like this\n"
        "   - Is this worth building for career growth? (one clear verdict)\n"
        "Focus on Indian tech market context (Bangalore, Hyderabad, Mumbai).\n"
        "Return ONLY valid JSON."
    )

    result = await call_ai(prompt, system, cache_ttl=3600)

    return result or {
        "missing_tech": [],
        "reasoning": "Analysis unavailable. Please try again.",
        "market_relevance": {"score": 0, "label": "Unknown", "summary": "Market analysis unavailable.", "top_industries": [], "similar_products": [], "hiring_demand": "Unknown", "verdict": "Unable to analyze market demand."},
    }


async def generate_project_blueprint(
    skill_matrix: dict[str, float],
) -> dict:
    """
    Suggest a project tailored to the user's current skill levels.

    Returns:
        {
          "title": "...",
          "description": "...",
          "tech_stack": ["..."],
          "skills_practiced": ["..."],
          "estimated_days": 14
        }
    """
    # Only include skills with enough confidence (score > 0.3)
    meaningful = {k: round(v, 2) for k, v in skill_matrix.items() if v > 0.3}

    system = (
        "You are a senior developer mentor recommending a portfolio project. "
        "Tailor the project to the developer's actual skill level — not too easy, not overwhelming. "
        "Return ONLY valid JSON matching this schema: "
        '{"title": "string", "description": "string", "tech_stack": ["string"], '
        '"skills_practiced": ["string"], "estimated_days": integer}'
    )

    prompt = (
        f"Developer skill matrix (skill: proficiency 0.0-1.0): {meaningful}\n\n"
        "Recommend ONE project that:\n"
        "- Uses their strongest skills as the backbone\n"
        "- Stretches their weaker skills through practice\n"
        "- Is portfolio-worthy and completable in under 30 days\n"
        "- Has a specific, concrete name (not 'Todo App')\n"
        "Return ONLY valid JSON."
    )

    result = await call_ai(prompt, system, cache_ttl=1800)

    return result or {
        "title": "API Gateway with Rate Limiting",
        "description": "Build a reverse proxy with per-user rate limiting, JWT auth, and request logging.",
        "tech_stack": ["FastAPI", "Redis", "PostgreSQL", "Docker"],
        "skills_practiced": list(meaningful.keys())[:4],
        "estimated_days": 14,
    }


async def analyze_team_gaps(
    team_skills: dict[str, dict[str, float]],
) -> dict:
    """
    Identify role gaps in a team based on aggregated skill scores.

    Args:
        team_skills: {username: {skill_name: score}}

    Returns:
        {
          "gaps": [{"role": "DevOps Engineer", "missing_skills": ["Kubernetes"], "covered": false}],
          "recommendation": "..."
        }
    """
    system = (
        "You are an engineering team lead analyzing skill coverage for a software team. "
        "Identify which engineering roles are well-covered and which are gaps. "
        "Return ONLY valid JSON matching this schema: "
        '{"gaps": [{"role": "string", "missing_skills": ["string"], "covered": boolean}], '
        '"recommendation": "string"}'
    )

    prompt = (
        f"Team skill matrix (member: skill scores): {team_skills}\n\n"
        "Analyze coverage for these roles: Backend Engineer, Frontend Engineer, "
        "DevOps/Infrastructure, Data Engineer, Security Engineer.\n"
        "A role is 'covered' if at least one team member scores > 0.6 in its core skills.\n"
        "Return ONLY valid JSON."
    )

    result = await call_ai(prompt, system, cache_ttl=900)  # 15 min cache for teams

    return result or {
        "gaps": [],
        "recommendation": "Team analysis unavailable. Please try again.",
    }