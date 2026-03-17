"""
services/career_gps_service.py

Data sources:
  1. GitHub API — search repos/topics by skill to estimate demand
  2. Groq AI    — enrich with market intelligence, salary ranges, gap analysis
  3. Redis cache — 2hr TTL so we don't hammer APIs
"""

import httpx
from loguru import logger

from redis_client import cache_get, cache_set
from services.ai_service import call_ai

_GH_API = "https://api.github.com"
_GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

# Job demand index — estimated monthly job postings per skill
# Based on industry data (Stack Overflow survey, LinkedIn reports)
# Used as baseline when GitHub API is unavailable
_BASELINE_DEMAND: dict[str, int] = {
    "Python":        95000,
    "JavaScript":    88000,
    "TypeScript":    72000,
    "Java":          81000,
    "Go":            42000,
    "Rust":          18000,
    "C++":           55000,
    "C#":            61000,
    "Ruby":          22000,
    "PHP":           38000,
    "Swift":         28000,
    "Kotlin":        31000,
    "React":         74000,
    "Node.js":       65000,
    "FastAPI":       18000,
    "Django":        25000,
    "Vue":           32000,
    "Angular":       38000,
    "Spring":        44000,
    "PostgreSQL":    58000,
    "MySQL":         62000,
    "Redis":         45000,
    "MongoDB":       41000,
    "Docker":        71000,
    "Kubernetes":    52000,
    "AWS":           84000,
    "Terraform":     38000,
    "GraphQL":       28000,
    "Machine Learning": 55000,
}

# Salary ranges by skill + location (USD annual)
# India salaries in INR (annual)
_INDIA_SALARY_DATA: dict[str, tuple] = {
    "Python":           (800000, 2500000),
    "JavaScript":       (700000, 2200000),
    "TypeScript":       (900000, 2800000),
    "Go":               (1200000, 3500000),
    "Rust":             (1500000, 4000000),
    "Java":             (800000, 2800000),
    "React":            (800000, 2500000),
    "Node.js":          (700000, 2200000),
    "FastAPI":          (900000, 2600000),
    "Django":           (800000, 2400000),
    "PostgreSQL":       (900000, 2500000),
    "MongoDB":          (800000, 2300000),
    "Docker":           (1000000, 3000000),
    "Kubernetes":       (1200000, 3500000),
    "AWS":              (1100000, 3200000),
    "Machine Learning": (1200000, 4000000),
    "DevOps":           (1000000, 3200000),
}
_INDIA_DEFAULT_SALARY = (700000, 2000000)

_SALARY_DATA: dict[str, dict] = {
    "Python":     {"USA": (95000, 175000), "UK": (65000, 120000), "remote": (75000, 150000), "EU": (60000, 110000), "India": (800000, 2500000)},
    "JavaScript": {"USA": (85000, 160000), "UK": (55000, 110000), "remote": (65000, 140000), "EU": (55000, 100000), "India": (700000, 2200000)},
    "TypeScript": {"USA": (90000, 170000), "UK": (60000, 115000), "remote": (70000, 145000), "EU": (58000, 105000), "India": (900000, 2800000)},
    "Go":         {"USA": (110000, 195000), "UK": (70000, 135000), "remote": (85000, 165000), "EU": (70000, 125000), "India": (1200000, 3500000)},
    "Rust":       {"USA": (120000, 210000), "UK": (75000, 145000), "remote": (90000, 175000), "EU": (75000, 135000), "India": (1500000, 4000000)},
    "Kubernetes": {"USA": (115000, 200000), "UK": (72000, 140000), "remote": (88000, 170000), "EU": (72000, 130000), "India": (1200000, 3500000)},
    "AWS":        {"USA": (105000, 190000), "UK": (68000, 130000), "remote": (82000, 160000), "EU": (68000, 120000), "India": (1100000, 3200000)},
    "Machine Learning": {"USA": (125000, 220000), "UK": (80000, 155000), "remote": (95000, 185000), "EU": (80000, 145000), "India": (1200000, 4000000)},
}

_DEFAULT_SALARY = {"USA": (80000, 140000), "UK": (50000, 95000), "remote": (60000, 120000), "EU": (50000, 90000), "India": (700000, 2000000)}


# ── GitHub demand fetcher ─────────────────────────────────────────────────────

async def _fetch_github_demand(skills: list[str]) -> dict[str, int]:
    """
    Use GitHub topic search to estimate skill demand.
    More repos + recent activity = higher demand signal.
    Cached for 2 hours to avoid rate limits.
    """
    cache_key = f"gps:gh_demand:{'-'.join(sorted(skills[:6]))}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    demand = {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for skill in skills[:8]:  # limit to avoid rate limiting
                topic = skill.lower().replace(" ", "-").replace(".", "").replace("#", "sharp")
                try:
                    resp = await client.get(
                        f"{_GH_API}/search/repositories",
                        headers=_GH_HEADERS,
                        params={
                            "q": f"topic:{topic} pushed:>2024-01-01",
                            "sort": "stars",
                            "per_page": 1,
                        },
                    )
                    if resp.status_code == 200:
                        count = resp.json().get("total_count", 0)
                        # Scale repo count to estimated job demand
                        demand[skill] = min(count * 8, 150000)
                    else:
                        demand[skill] = _BASELINE_DEMAND.get(skill, 20000)
                except Exception:
                    demand[skill] = _BASELINE_DEMAND.get(skill, 20000)

        await cache_set(cache_key, demand, ttl=7200)
    except Exception as exc:
        logger.warning("GitHub demand fetch failed", error=str(exc))
        demand = {s: _BASELINE_DEMAND.get(s, 20000) for s in skills}

    # Fill any missing with baseline
    for skill in skills:
        if skill not in demand:
            demand[skill] = _BASELINE_DEMAND.get(skill, 20000)

    return demand


# ── Market analysis ───────────────────────────────────────────────────────────

async def get_market_analysis(skill_matrix: dict[str, float], location: str) -> dict:
    if not skill_matrix:
        return _empty_market()

    cache_key = f"gps:market:{location}:{'-'.join(sorted(skill_matrix.keys())[:6])}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    skills = list(skill_matrix.keys())
    gh_demand = await _fetch_github_demand(skills)

    # Calculate jobs qualified for (score >= 0.5 = entry, >= 0.7 = mid, >= 0.85 = senior)
    qualified_entry  = sum(1 for s, v in skill_matrix.items() if v >= 0.5)
    qualified_mid    = sum(1 for s, v in skill_matrix.items() if v >= 0.7)
    qualified_senior = sum(1 for s, v in skill_matrix.items() if v >= 0.85)

    # Estimate total jobs
    total_demand = sum(gh_demand.get(s, _BASELINE_DEMAND.get(s, 20000)) for s in skills)
    qualified_jobs = int(total_demand * (qualified_mid / max(len(skills), 1)) * 0.3)

    # Skill demand bubbles — normalize 0-100
    max_demand = max(gh_demand.values()) if gh_demand else 1
    skill_bubbles = [
        {
            "skill": skill,
            "score": round(skill_matrix.get(skill, 0) * 100),
            "demand": gh_demand.get(skill, _BASELINE_DEMAND.get(skill, 20000)),
            "demand_pct": round(gh_demand.get(skill, 0) / max_demand * 100),
            "eligible": skill_matrix.get(skill, 0) >= 0.5,
        }
        for skill in skills
    ]
    skill_bubbles.sort(key=lambda x: x["demand"], reverse=True)

    # Top demanded skills user doesn't have
    all_demanded = sorted(_BASELINE_DEMAND.items(), key=lambda x: x[1], reverse=True)
    missing_hot = [
        {"skill": s, "demand": d, "demand_pct": round(d / max(max_demand, 1) * 100)}
        for s, d in all_demanded
        if s not in skill_matrix and d > 30000
    ][:6]

    # Location job multipliers
    loc_multipliers = {
        "usa": 1.0, "us": 1.0, "united states": 1.0,
        "uk": 0.4, "united kingdom": 0.4, "london": 0.35,
        "eu": 0.55, "europe": 0.55, "germany": 0.28, "berlin": 0.22,
        "canada": 0.35, "australia": 0.25,
        "india": 1.2, "bangalore": 1.2, "bengaluru": 1.2,
        "mumbai": 1.0, "delhi": 1.0, "hyderabad": 1.1,
        "pune": 0.9, "chennai": 0.9, "noida": 0.95,
        "gurugram": 1.0, "gurgaon": 1.0, "kochi": 0.8,
        "remote": 0.85, "worldwide": 1.0, "global": 1.0,
    }
    loc_key = location.lower().strip()
    multiplier = loc_multipliers.get(loc_key, 0.5)
    adjusted_jobs = int(qualified_jobs * multiplier)

    result = {
        "summary": {
            "total_skills": len(skills),
            "qualified_entry": qualified_entry,
            "qualified_mid": qualified_mid,
            "qualified_senior": qualified_senior,
            "estimated_jobs": adjusted_jobs,
            "location": location,
            "market_coverage": round(qualified_mid / max(len(skills), 1) * 100),
        },
        "skill_bubbles": skill_bubbles,
        "missing_hot_skills": missing_hot,
        "location_breakdown": _location_breakdown(adjusted_jobs, loc_key),
    }

    await cache_set(cache_key, result, ttl=3600)
    return result


def _location_breakdown(base_jobs: int, location: str) -> list[dict]:
    """Estimated job distribution across locations — India-first."""
    loc_lower = location.lower()
    is_india = any(x in loc_lower for x in ["india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune", "chennai", "noida", "gurugram"])

    if is_india:
        return [
            {"region": "🇮🇳 Bangalore", "jobs": int(base_jobs * 1.0), "avg_salary": "₹12L–₹35L", "growth": "+22%"},
            {"region": "🇮🇳 Hyderabad", "jobs": int(base_jobs * 0.85), "avg_salary": "₹10L–₹30L", "growth": "+18%"},
            {"region": "🇮🇳 Mumbai", "jobs": int(base_jobs * 0.75), "avg_salary": "₹10L–₹28L", "growth": "+15%"},
            {"region": "🇮🇳 Delhi NCR", "jobs": int(base_jobs * 0.80), "avg_salary": "₹10L–₹30L", "growth": "+17%"},
            {"region": "🇮🇳 Pune", "jobs": int(base_jobs * 0.65), "avg_salary": "₹8L–₹25L", "growth": "+14%"},
            {"region": "🌍 Remote (India)", "jobs": int(base_jobs * 0.70), "avg_salary": "₹15L–₹45L", "growth": "+35%"},
        ]

    return [
        {"region": "🇮🇳 India", "jobs": int(base_jobs * 0.9), "avg_salary": "₹8L–₹35L", "growth": "+22%"},
        {"region": "🇺🇸 USA", "jobs": int(base_jobs * 1.0), "avg_salary": "$120k–$180k", "growth": "+12%"},
        {"region": "🌍 Remote", "jobs": int(base_jobs * 0.85), "avg_salary": "$90k–$150k", "growth": "+28%"},
        {"region": "🇬🇧 UK", "jobs": int(base_jobs * 0.4), "avg_salary": "£65k–£110k", "growth": "+8%"},
        {"region": "🇪🇺 Europe", "jobs": int(base_jobs * 0.55), "avg_salary": "€60k–€100k", "growth": "+15%"},
        {"region": "🇨🇦 Canada", "jobs": int(base_jobs * 0.35), "avg_salary": "C$90k–C$140k", "growth": "+10%"},
    ]


def _empty_market() -> dict:
    return {
        "summary": {"total_skills": 0, "qualified_entry": 0, "qualified_mid": 0,
                    "qualified_senior": 0, "estimated_jobs": 0, "location": "remote", "market_coverage": 0},
        "skill_bubbles": [],
        "missing_hot_skills": [],
        "location_breakdown": [],
    }


# ── URL builder — never trust AI for URLs ─────────────────────────────────────

import urllib.parse as _urlparse

def _build_url(source: str, title: str, skill: str, location: str) -> str:
    """Build a guaranteed absolute searchable URL. Called server-side only."""
    t = _urlparse.quote_plus(title)
    s = _urlparse.quote_plus(skill)
    l = _urlparse.quote_plus(location)
    s_naukri = skill.lower().replace(" ", "-").replace("/", "-")
    l_naukri = location.lower().replace(" ", "-").replace(",", "").strip("-")
    if source == "Naukri":
        return f"https://www.naukri.com/{s_naukri}-jobs-in-{l_naukri}"
    if source == "Indeed":
        return f"https://in.indeed.com/jobs?q={t}+{s}&l={l}"
    # LinkedIn default
    return f"https://www.linkedin.com/jobs/search/?keywords={t}+{s}&location={l}"


# ── Live opportunities ────────────────────────────────────────────────────────

async def get_live_opportunities(skill_matrix: dict[str, float], location: str, limit: int) -> dict:
    """
    AI-powered job listings with server-built URLs (never from AI).
    Cache key versioned so stale results are ignored after code changes.
    """
    if not skill_matrix:
        return {"jobs": [], "total": 0}

    # v4 in key forces cache miss on old broken data
    cache_key = f"gps:jobs:v4:{location}:{'-'.join(sorted(skill_matrix.keys())[:5])}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    top_skills = sorted(skill_matrix.items(), key=lambda x: x[1], reverse=True)[:5]
    skill_names = [s[0] for s in top_skills]

    india_cities = ["bangalore", "bengaluru", "hyderabad", "mumbai", "pune",
                    "delhi", "noida", "gurugram", "chennai", "kochi", "remote", "india"]
    loc_lower  = location.lower()
    is_india   = any(c in loc_lower for c in india_cities)
    loc_label  = location if loc_lower != "remote" else "Remote (India)"

    salary_hint = (
        "₹ Lakhs/year — ₹8L–₹18L junior, ₹18L–₹35L mid, ₹35L–₹65L senior"
        if is_india else
        "USD/year — $80k–$120k junior, $120k–$160k mid, $160k–$220k senior"
    )

    # Ask AI only for title/company/salary/match — NOT URLs
    system = (
        "You are a tech recruiter for Indian developers. "
        "Generate varied realistic job listings — NEVER repeat 'Senior X Developer'. "
        "Title rules: Python/FastAPI → Backend Engineer or Python Engineer; "
        "ML/TensorFlow/PyTorch → ML Engineer, AI Engineer, NLP Engineer, MLOps Engineer; "
        "React/Vue → Frontend Engineer or UI Engineer; "
        "Docker/K8s → DevOps Engineer, Platform Engineer, SRE; "
        "PostgreSQL/Redis → Data Engineer; mixed stack → Full Stack Engineer. "
        "DO NOT include any url field — URLs are added separately. "
        "Return ONLY valid JSON with this exact schema: "
        '{"jobs": [{"title": "string", "company": "string", '
        '"company_type": "startup|scaleup|enterprise|faang|service", '
        '"location": "string", "salary_range": "string", '
        '"required_skills": ["string"], "match_score": 85, '
        '"match_reason": "string", "posted_days_ago": 3, "applicants": 45}]}'
    )

    prompt = (
        f"Skills (name: score 0.0-1.0): {dict(top_skills)}\n"
        f"Location: {loc_label}\n"
        f"Salary: {salary_hint}\n\n"
        f"Generate exactly {min(limit, 8)} jobs:\n"
        "  • 3 qualify NOW (match_score 80-95)\n"
        "  • 3 stretch roles (match_score 60-79)\n"
        "  • 2 aspirational (match_score 40-59)\n\n"
        f"Skills to base titles on: {', '.join(skill_names)}\n"
        "Companies to use: Razorpay, CRED, Swiggy, Zomato, Meesho, PhonePe, Groww, "
        "Paytm, Flipkart, Freshworks, Zoho, Ola, Byju's, Unacademy, Infosys, TCS, "
        "Wipro, ThoughtWorks, Accenture, MakeMyTrip.\n"
        "Return ONLY valid JSON. No url field."
    )

    result  = await call_ai(prompt, system, cache_ttl=0)   # always fresh
    jobs    = result.get("jobs", []) if result else []

    if not jobs:
        jobs = _fallback_jobs(skill_names, loc_label, is_india)

    # Attach URLs server-side — rotate sources across listings
    sources = ["LinkedIn", "Naukri", "Indeed"]
    primary = skill_names[0] if skill_names else "developer"
    for i, job in enumerate(jobs):
        src = sources[i % len(sources)]
        job["source"] = src
        job["url"]    = _build_url(src, job.get("title", "Software Engineer"), primary, loc_label)

    jobs.sort(key=lambda x: x.get("match_score", 0), reverse=True)

    output = {"jobs": jobs[:limit], "total": len(jobs)}
    await cache_set(cache_key, output, ttl=1800)
    return output


def _fallback_jobs(skills: list[str], location: str, is_india: bool = True) -> list[dict]:
    """Varied fallback jobs when AI fails — proper titles, no URLs (added later)."""
    primary   = skills[0] if skills else "Python"
    secondary = skills[1] if len(skills) > 1 else primary

    title_map = {
        "Python": "Backend Engineer", "Machine Learning": "ML Engineer",
        "JavaScript": "Frontend Engineer", "TypeScript": "Full Stack Engineer",
        "React": "Frontend Developer", "FastAPI": "Python Backend Engineer",
        "Node.js": "Node.js Engineer", "Docker": "DevOps Engineer",
        "Kubernetes": "Platform Engineer", "PostgreSQL": "Data Engineer",
        "AWS": "Cloud Engineer", "Go": "Backend Engineer",
        "TensorFlow": "ML Engineer", "PyTorch": "AI/ML Developer",
    }
    title1 = title_map.get(primary,   f"{primary} Engineer")
    title2 = title_map.get(secondary, "Full Stack Engineer")

    s1 = "₹18L–₹32L" if is_india else "$110k–$150k"
    s2 = "₹12L–₹22L" if is_india else "$80k–$120k"
    s3 = "₹8L–₹16L"  if is_india else "$65k–$95k"

    return [
        {"title": title1,            "company": "Razorpay",  "company_type": "scaleup",
         "location": location, "salary_range": s1, "required_skills": skills[:3],
         "match_score": 82, "match_reason": f"Strong {primary} skills match this role",
         "posted_days_ago": 2, "applicants": 43},
        {"title": title2,            "company": "Swiggy",    "company_type": "startup",
         "location": location, "salary_range": s2, "required_skills": skills[:2],
         "match_score": 66, "match_reason": f"Good {primary} base, needs {secondary} depth",
         "posted_days_ago": 5, "applicants": 87},
        {"title": "Software Engineer II", "company": "Freshworks", "company_type": "enterprise",
         "location": location, "salary_range": s3, "required_skills": skills[:2],
         "match_score": 55, "match_reason": "Entry-level role to build industry experience",
         "posted_days_ago": 7, "applicants": 120},
    ]


# ── Skill gap analysis ────────────────────────────────────────────────────────

async def get_skill_gap_analysis(skill_matrix: dict[str, float], location: str) -> dict:
    """AI-powered analysis of which skills to learn to unlock the most jobs."""
    cache_key = f"gps:gaps:{location}:{'-'.join(sorted(skill_matrix.keys())[:6])}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    top_skills = sorted(skill_matrix.items(), key=lambda x: x[1], reverse=True)[:8]

    system = (
        "You are a career advisor analyzing job market skill gaps. "
        "Return ONLY valid JSON: "
        '{"gaps": [{"skill": "string", "current_score": 0.0, "target_score": 0.75, '
        '"jobs_unlocked": 1250, "effort_weeks": 4, "priority": "high|medium|low", '
        '"why": "string", "learning_path": ["resource1", "resource2"]}], '
        '"quick_wins": ["skill1", "skill2"], "career_advice": "string"}'
    )

    prompt = (
        f"Developer current skills: {dict(top_skills)}\n"
        f"Target market: {location}\n\n"
        "Identify the TOP 5 skill improvements that would unlock the most job opportunities.\n"
        "For each gap:\n"
        "- Which skill to improve and by how much\n"
        "- How many additional jobs it would unlock (realistic estimate)\n"
        "- Effort in weeks to reach target\n"
        "- Priority (high = big impact, low effort)\n"
        "- 2 specific learning resources\n"
        "Also identify 3 'quick wins' — skills close to threshold that need minimal work.\n"
        "Return ONLY valid JSON."
    )

    result = await call_ai(prompt, system, cache_ttl=3600)

    if not result or "gaps" not in result:
        result = _fallback_gaps(skill_matrix)

    await cache_set(cache_key, result, ttl=3600)
    return result


def _fallback_gaps(skill_matrix: dict[str, float]) -> dict:
    weak = [(s, v) for s, v in skill_matrix.items() if v < 0.7]
    weak.sort(key=lambda x: x[1], reverse=True)
    gaps = []
    for skill, score in weak[:3]:
        gaps.append({
            "skill": skill,
            "current_score": score,
            "target_score": 0.75,
            "jobs_unlocked": 800,
            "effort_weeks": 4,
            "priority": "high" if score > 0.4 else "medium",
            "why": f"Improving {skill} to 75% would significantly expand your job options",
            "learning_path": [f"Official {skill} documentation", f"{skill} on freeCodeCamp"],
        })
    return {"gaps": gaps, "quick_wins": [s for s, v in weak[:3] if v > 0.5], "career_advice": "Focus on depth over breadth."}


# ── Salary hints ──────────────────────────────────────────────────────────────

async def get_salary_hints(skill_matrix: dict[str, float], location: str) -> dict:
    """Estimate salary range based on skill matrix and location."""
    cache_key = f"gps:salary:{location}:{'-'.join(sorted(skill_matrix.keys())[:6])}"
    cached = await cache_get(cache_key)
    if cached:
        return cached

    loc_key = location.lower()
    # Normalize location key
    loc_norm = "USA"
    if any(x in loc_key for x in ["india", "bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune", "chennai", "kolkata", "noida", "gurugram", "gurgaon", "kochi", "trivandrum", "thiruvananthapuram"]): loc_norm = "India"
    elif any(x in loc_key for x in ["uk", "london", "england"]): loc_norm = "UK"
    elif any(x in loc_key for x in ["eu", "europe", "germany", "france"]): loc_norm = "EU"
    elif "remote" in loc_key: loc_norm = "remote"

    # Calculate weighted salary based on skill scores
    total_weight = 0
    weighted_min = 0
    weighted_max = 0

    for skill, score in skill_matrix.items():
        salary_range = _SALARY_DATA.get(skill, _DEFAULT_SALARY).get(loc_norm, (70000, 120000))
        weight = score  # higher skill score = more weight
        weighted_min += salary_range[0] * weight
        weighted_max += salary_range[1] * weight
        total_weight += weight

    if total_weight > 0:
        base_min = int(weighted_min / total_weight)
        base_max = int(weighted_max / total_weight)
    else:
        base_min, base_max = 70000, 120000

    # Seniority multiplier based on average skill score
    avg_score = sum(skill_matrix.values()) / len(skill_matrix) if skill_matrix else 0.5
    if avg_score >= 0.8:
        level = "Senior"
        mult_min, mult_max = 1.2, 1.35
    elif avg_score >= 0.6:
        level = "Mid-level"
        mult_min, mult_max = 1.0, 1.15
    elif avg_score >= 0.4:
        level = "Junior"
        mult_min, mult_max = 0.75, 0.9
    else:
        level = "Entry"
        mult_min, mult_max = 0.6, 0.75

    final_min = int(base_min * mult_min / 1000) * 1000
    final_max = int(base_max * mult_max / 1000) * 1000

    # What would increase salary most
    currency = {"USA": "$", "UK": "£", "EU": "€", "remote": "$", "India": "₹"}.get(loc_norm, "$")
    improvement_tips = []
    for skill, score in sorted(skill_matrix.items(), key=lambda x: x[1]):
        if score < 0.7 and skill in _SALARY_DATA:
            top_range = _SALARY_DATA[skill].get(loc_norm, (0, 0))
            improvement_tips.append({
                "skill": skill,
                "current": round(score * 100),
                "target": 75,
                "salary_boost": f"+{currency}{round((top_range[1] - base_max) * 0.15 / 1000)}k",
            })
    improvement_tips = improvement_tips[:4]

    result = {
        "level": level,
        "salary_min": final_min,
        "salary_max": final_max,
        "currency": currency,
        "location": location,
        "formatted": f"{currency}{final_min//1000}k – {currency}{final_max//1000}k",
        "avg_skill_score": round(avg_score * 100),
        "improvement_tips": improvement_tips,
        "percentile": min(95, int(avg_score * 110)),
    }

    await cache_set(cache_key, result, ttl=7200)
    return result