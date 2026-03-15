"""
github_service.py — GitHub API integration.

  - Scan a user's repos to pre-seed their skill matrix from languages used
  - Search GitHub for help-wanted / good-first-issue issues matching user skills
  - All calls are cached in Redis to avoid GitHub rate limits (60 req/hr unauthed,
    5000 req/hr with a user token)
"""

import httpx
from loguru import logger

from redis_client import cache_get, cache_set

_GH_API = "https://api.github.com"
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Map GitHub language names → our internal skill names
_LANGUAGE_MAP: dict[str, str] = {
    "Python":     "Python",
    "JavaScript": "JavaScript",
    "TypeScript": "TypeScript",
    "Go":         "Go",
    "Rust":       "Rust",
    "Java":       "Java",
    "Kotlin":     "Kotlin",
    "Swift":      "Swift",
    "Ruby":       "Ruby",
    "PHP":        "PHP",
    "C#":         "C#",
    "C++":        "C++",
    "Shell":      "Bash/Shell",
    "Dockerfile": "Docker",
    "HCL":        "Terraform",
}


# ── Repo scanner ──────────────────────────────────────────────────────────────

async def scan_user_repos(github_token: str) -> dict[str, float]:
    """
    Fetch the authenticated user's repos and aggregate language bytes.
    Returns a dict of {skill_name: initial_score} for pre-seeding.

    Score is logarithmically scaled so having 10x more lines of Python
    doesn't make you 10x better — it just pushes the seed score higher
    within a modest range (0.3–0.65). Real scores come from assessments.
    """
    import math

    cache_key = f"gh:repos:{github_token[:16]}"  # partial token as key
    cached = await cache_get(cache_key)
    if cached:
        return cached

    headers = {**_HEADERS, "Authorization": f"Bearer {github_token}"}
    language_bytes: dict[str, int] = {}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Fetch up to 100 repos (public + private)
            resp = await client.get(
                f"{_GH_API}/user/repos",
                headers=headers,
                params={"per_page": 100, "sort": "pushed", "type": "owner"},
            )
            resp.raise_for_status()

            for repo in resp.json():
                lang = repo.get("language")
                if lang and lang in _LANGUAGE_MAP:
                    # Use repo size as a proxy for bytes (faster than per-repo lang API)
                    language_bytes[lang] = (
                        language_bytes.get(lang, 0) + repo.get("size", 0)
                    )

    except httpx.HTTPError as exc:
        logger.warning("GitHub repo scan failed", error=str(exc))
        return {}

    if not language_bytes:
        return {}

    # Normalise to seed scores (0.3–0.65 range)
    max_bytes = max(language_bytes.values())
    result: dict[str, float] = {}
    for lang, size in language_bytes.items():
        skill = _LANGUAGE_MAP[lang]
        ratio = size / max_bytes
        # log scale: even tiny usage gives 0.3, dominant lang gives 0.65
        score = 0.30 + 0.35 * math.log1p(ratio * 9) / math.log1p(9)
        result[skill] = round(score, 3)

    await cache_set(cache_key, result, ttl=3600)
    return result


# ── Help-wanted issue search ──────────────────────────────────────────────────

async def search_help_wanted_issues(
    skills: list[str],
    label: str = "help wanted",
    max_results: int = 20,
) -> list[dict]:
    """
    Search GitHub for open issues with help-wanted/good-first-issue labels
    that match the user's top skills.

    Returns a list of enriched issue dicts ready for the frontend.
    """
    if not skills:
        return []

    cache_key = f"gh:issues:{'-'.join(sorted(skills[:5]))}:{label}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    # Build a GitHub search query combining skill-related terms and the label
    skill_terms = " OR ".join(f'"{s}"' for s in skills[:6])
    query = f'({skill_terms}) label:"{label}" state:open type:issue'

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{_GH_API}/search/issues",
                headers=_HEADERS,
                params={
                    "q": query,
                    "sort": "reactions",
                    "order": "desc",
                    "per_page": max_results,
                },
            )
            resp.raise_for_status()
            raw_issues = resp.json().get("items", [])

    except httpx.HTTPError as exc:
        logger.warning("GitHub issue search failed", error=str(exc))
        return _fallback_issues(skills, label)

    issues = []
    for item in raw_issues:
        repo_full = item.get("repository_url", "").split("repos/")[-1]
        labels = [lb["name"] for lb in item.get("labels", [])]

        # Infer which of the user's skills this issue matches
        title_body = (item.get("title", "") + " " + (item.get("body") or "")).lower()
        matched_skills = [s for s in skills if s.lower() in title_body]

        # Compute a rough match score for sorting
        match_pct = min(95, 40 + len(matched_skills) * 18 + min(item.get("reactions", {}).get("total_count", 0), 20))

        issues.append({
            "id":       item["id"],
            "title":    item["title"],
            "url":      item["html_url"],
            "repo":     repo_full,
            "label":    "good-first-issue" if "good first issue" in " ".join(labels).lower() else "help-wanted",
            "language": _infer_language(repo_full, title_body),
            "skills":   matched_skills or skills[:2],
            "stars":    0,   # requires extra API call — skip for rate limits
            "comments": item.get("comments", 0),
            "match":    match_pct,
            "created_at": item.get("created_at", ""),
        })

    # Sort by match score descending
    issues.sort(key=lambda x: x["match"], reverse=True)
    await cache_set(cache_key, issues, ttl=900)  # 15 min cache
    return issues


def _infer_language(repo: str, text: str) -> str:
    """Heuristic language detection from repo name and issue text."""
    hints = {
        "python": "Python", "fastapi": "Python", "django": "Python", "flask": "Python",
        "javascript": "JavaScript", "node": "JavaScript", "react": "JavaScript",
        "typescript": "TypeScript", "next": "TypeScript",
        "go": "Go", "golang": "Go",
        "rust": "Rust",
        "java": "Java", "spring": "Java",
        "ruby": "Ruby", "rails": "Ruby",
    }
    combined = (repo + " " + text).lower()
    for hint, lang in hints.items():
        if hint in combined:
            return lang
    return "Various"


def _fallback_issues(skills: list[str], label: str) -> list[dict]:
    """Static fallback issues when GitHub API is unavailable."""
    return [
        {
            "id": 1,
            "title": f"Improve {skills[0]} documentation and add examples",
            "url": "https://github.com/explore",
            "repo": "open-source/project",
            "label": "help-wanted",
            "language": skills[0] if skills else "Python",
            "skills": skills[:2],
            "stars": 0,
            "comments": 0,
            "match": 75,
            "created_at": "",
        }
    ]