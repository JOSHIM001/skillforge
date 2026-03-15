"""
routers/resume.py — Resume Intelligence

POST /resume/upload        → upload PDF/DOCX, extract + analyze skills
POST /resume/text          → paste plain text resume
GET  /resume/analysis      → get stored resume analysis for current user
GET  /resume/job-match     → match resume against job market (Career GPS integration)
DELETE /resume             → clear resume data
"""

import io
import uuid
import re
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from middleware.auth_middleware import CurrentUser
from models.skill import SkillProfile
from models.user import User
from redis_client import cache_get, cache_set, cache_delete
from services.ai_service import call_ai

router = APIRouter()

# Max file size: 5MB
MAX_FILE_SIZE = 5 * 1024 * 1024


# ── Schemas ───────────────────────────────────────────────────────────────────

class ResumeTextRequest(BaseModel):
    text: str
    linkedin_url: str | None = None


class ExtractedSkill(BaseModel):
    name: str
    confidence: float        # 0.0–1.0 how confident AI is this is real skill
    years_mentioned: float | None
    context: str             # where it was found e.g. "3 years Python at TechCorp"
    verified: bool = False   # True once they pass an assessment


class ResumeAnalysis(BaseModel):
    skills: list[ExtractedSkill]
    job_titles: list[str]
    total_experience_years: float
    education: list[str]
    summary: str
    strongest_areas: list[str]
    skill_gaps_vs_market: list[str]
    resume_score: int        # 0–100 overall resume quality score
    honest_score: int        # % of claimed skills that are verified
    analyzed_at: str


# ── Text extractors ───────────────────────────────────────────────────────────

def _extract_pdf_text(file_bytes: bytes) -> str:
    try:
        import PyPDF2
        reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        return text.strip()
    except Exception as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Could not parse PDF: {e}")


def _extract_docx_text(file_bytes: bytes) -> str:
    try:
        import docx
        doc = docx.Document(io.BytesIO(file_bytes))
        text = "\n".join(para.text for para in doc.paragraphs if para.text.strip())
        return text.strip()
    except Exception as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Could not parse DOCX: {e}")


async def _fetch_linkedin_text(url: str) -> str:
    """
    LinkedIn blocks scraping, so we use AI to simulate profile analysis
    based on the URL pattern (public profile slug).
    """
    slug = url.split("/in/")[-1].strip("/").split("/")[0] if "/in/" in url else "unknown"
    return f"LinkedIn profile: {slug}. Public profile URL provided for analysis."


# ── Core AI analyzer ─────────────────────────────────────────────────────────

async def _analyze_resume_text(text: str) -> dict:
    """Use Groq to extract structured data from resume text."""
    system = (
        "You are an expert technical recruiter and resume analyzer. "
        "Extract ALL technical skills, tools, frameworks, and technologies from the resume. "
        "Be specific — 'React' not 'frontend', 'PostgreSQL' not 'databases'. "
        "Return ONLY valid JSON matching this schema exactly: "
        '{"skills": [{"name": "Python", "confidence": 0.9, "years_mentioned": 3.0, "context": "3 years Python at TechCorp"}], '
        '"job_titles": ["Senior Backend Engineer"], '
        '"total_experience_years": 5.0, '
        '"education": ["BSc Computer Science, MIT"], '
        '"summary": "Experienced backend developer...", '
        '"strongest_areas": ["Python", "Distributed Systems"], '
        '"skill_gaps_vs_market": ["Kubernetes", "Go"], '
        '"resume_score": 78}'
    )

    prompt = (
        "Analyze this resume and extract all technical information.\n\n"
        "RESUME TEXT:\n"
        f"{text[:4000]}\n\n"  # Limit to 4000 chars to fit context
        "Extract:\n"
        "1. Every technical skill with confidence (0.9=explicitly stated with years, 0.6=mentioned once, 0.3=implied)\n"
        "2. Job titles held\n"
        "3. Total years of experience\n"
        "4. Education\n"
        "5. A 2-sentence professional summary\n"
        "6. Top 3 strongest technical areas\n"
        "7. Top 3 skills missing vs current market demand\n"
        "8. Resume quality score 0-100 (100=perfect formatting+content)\n"
        "Return ONLY valid JSON."
    )

    result = await call_ai(prompt, system, cache_ttl=0)

    if not result or "skills" not in result:
        return _fallback_analysis(text)

    return result


def _fallback_analysis(text: str) -> dict:
    """Keyword-based fallback if AI fails."""
    known_skills = [
        "Python", "JavaScript", "TypeScript", "Java", "Go", "Rust", "C++", "C#",
        "React", "Vue", "Angular", "Node.js", "FastAPI", "Django", "Flask", "Spring",
        "PostgreSQL", "MySQL", "Redis", "MongoDB", "SQLite",
        "Docker", "Kubernetes", "AWS", "GCP", "Azure", "Terraform",
        "Git", "Linux", "GraphQL", "REST", "gRPC",
        "Machine Learning", "TensorFlow", "PyTorch", "Pandas", "NumPy",
    ]
    text_lower = text.lower()
    found = [s for s in known_skills if s.lower() in text_lower]

    return {
        "skills": [{"name": s, "confidence": 0.6, "years_mentioned": None, "context": "Found in resume"} for s in found],
        "job_titles": [],
        "total_experience_years": 0.0,
        "education": [],
        "summary": "Resume analyzed via keyword extraction.",
        "strongest_areas": found[:3],
        "skill_gaps_vs_market": ["Kubernetes", "Go", "Rust"],
        "resume_score": 50,
    }


# ── Skill matrix updater ──────────────────────────────────────────────────────

async def _seed_skills_from_resume(
    user_id: uuid.UUID,
    extracted_skills: list[dict],
    db: AsyncSession,
) -> dict[str, str]:
    """
    Pre-seed skill profiles from resume.
    Only sets score if no existing assessed score is higher.
    Returns {skill_name: "added"|"updated"|"skipped"} status per skill.
    """
    status_map = {}

    for skill_data in extracted_skills:
        skill_name = skill_data.get("name", "").strip()
        ai_confidence = skill_data.get("confidence", 0.5)  # AI's confidence this is real
        years = skill_data.get("years_mentioned") or 0

        if not skill_name or len(skill_name) < 2:
            continue

        # Convert to initial score:
        # confidence * years-bonus → capped at 0.55 (resume claims are unverified)
        years_bonus = min(years * 0.03, 0.15)
        initial_score = min(0.55, ai_confidence * 0.45 + years_bonus + 0.1)

        # Check existing profile
        result = await db.execute(
            select(SkillProfile).where(
                SkillProfile.user_id == user_id,
                SkillProfile.skill_name == skill_name,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            if existing.confidence > 0.1:
                # Already assessed — don't overwrite with resume data
                status_map[skill_name] = "skipped"
                continue
            else:
                # Only has resume data — update
                existing.score = max(existing.score, initial_score)
                status_map[skill_name] = "updated"
        else:
            # New skill from resume — add with very low confidence
            profile = SkillProfile(
                user_id=user_id,
                skill_name=skill_name,
                score=initial_score,
                confidence=0.05,      # near-zero — marks it as "resume only, unverified"
                difficulty_level=1,
                last_assessed=None,
            )
            db.add(profile)
            status_map[skill_name] = "added"

    await db.flush()
    return status_map


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/upload", summary="Upload PDF or DOCX resume")
async def upload_resume(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
) -> dict:
    # Check file size
    file_bytes = await file.read()
    if len(file_bytes) > MAX_FILE_SIZE:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File too large (max 5MB)")

    # Extract text based on file type
    filename = (file.filename or "").lower()
    content_type = file.content_type or ""

    if filename.endswith(".pdf") or "pdf" in content_type:
        text = _extract_pdf_text(file_bytes)
    elif filename.endswith(".docx") or "word" in content_type or "openxml" in content_type:
        text = _extract_docx_text(file_bytes)
    else:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Only PDF and DOCX files are supported")

    if len(text) < 50:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Could not extract enough text from file")

    return await _process_resume(current_user, text, db)


@router.post("/text", summary="Paste resume as plain text or provide LinkedIn URL")
async def resume_from_text(
    body: ResumeTextRequest,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    text = body.text.strip()

    # If LinkedIn URL provided, append it
    if body.linkedin_url and "linkedin.com" in body.linkedin_url:
        linkedin_text = await _fetch_linkedin_text(body.linkedin_url)
        text = text + "\n\n" + linkedin_text if text else linkedin_text

    if len(text) < 30:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Resume text is too short")

    return await _process_resume(current_user, text, db)


async def _process_resume(current_user, text: str, db: AsyncSession) -> dict:
    """Shared processing logic for all resume input types."""

    # Run AI analysis
    analysis = await _analyze_resume_text(text)

    # Seed skills into matrix
    extracted_skills = analysis.get("skills", [])
    seed_status = await _seed_skills_from_resume(current_user.id, extracted_skills, db)

    added   = [s for s, st in seed_status.items() if st == "added"]
    updated = [s for s, st in seed_status.items() if st == "updated"]
    skipped = [s for s, st in seed_status.items() if st == "skipped"]

    # Store full analysis in Redis (24hr TTL)
    analysis["analyzed_at"] = datetime.now(timezone.utc).isoformat()
    analysis["honest_score"] = 0  # starts at 0, increases with assessments
    await cache_set(f"resume:{current_user.id}", analysis, ttl=86400)

    return {
        "success": True,
        "skills_found": len(extracted_skills),
        "skills_added": len(added),
        "skills_updated": len(updated),
        "skills_skipped": len(skipped),
        "added_skills": added,
        "job_titles": analysis.get("job_titles", []),
        "experience_years": analysis.get("total_experience_years", 0),
        "resume_score": analysis.get("resume_score", 0),
        "summary": analysis.get("summary", ""),
        "strongest_areas": analysis.get("strongest_areas", []),
        "message": f"Found {len(extracted_skills)} skills. Added {len(added)} new, skipped {len(skipped)} already assessed.",
    }


@router.get("/analysis", summary="Get stored resume analysis")
async def get_analysis(
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> dict:
    cached = await cache_get(f"resume:{current_user.id}")
    if not cached:
        return {"has_resume": False}

    # Calculate honest score: % of resume skills that are now verified
    skills = cached.get("skills", [])
    if not skills:
        return {"has_resume": True, **cached}

    skill_names = [s["name"] for s in skills]
    result = await db.execute(
        select(SkillProfile).where(
            SkillProfile.user_id == current_user.id,
            SkillProfile.skill_name.in_(skill_names),
            SkillProfile.confidence > 0.1,  # actually assessed
        )
    )
    verified_profiles = result.scalars().all()
    verified_names = {p.skill_name for p in verified_profiles}

    # Mark which skills are verified
    for skill in skills:
        skill["verified"] = skill["name"] in verified_names

    honest_score = round(len(verified_names) / len(skills) * 100) if skills else 0
    cached["honest_score"] = honest_score
    cached["verified_count"] = len(verified_names)
    cached["has_resume"] = True

    return cached


@router.get("/job-match", summary="Match resume skills against job market")
async def job_match(
    current_user: CurrentUser,
    location: str = "remote",
    db: AsyncSession = Depends(get_db),
) -> dict:
    cached = await cache_get(f"resume:{current_user.id}")
    if not cached:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No resume uploaded yet")

    # Only use VERIFIED skills (assessed at least once) for Career GPS
    skill_names = [s["name"] for s in cached.get("skills", [])]
    result = await db.execute(
        select(SkillProfile).where(
            SkillProfile.user_id == current_user.id,
            SkillProfile.skill_name.in_(skill_names),
            SkillProfile.confidence > 0.1,
        )
    )
    verified = result.scalars().all()

    if not verified:
        return {
            "message": "Complete at least 1 assessment to verify your resume skills before job matching.",
            "verified_skills": 0,
            "total_resume_skills": len(skill_names),
            "jobs": [],
        }

    verified_matrix = {p.skill_name: p.score for p in verified}

    # Use career GPS service
    from services.career_gps_service import get_live_opportunities, get_market_analysis
    market = await get_market_analysis(verified_matrix, location)
    jobs   = await get_live_opportunities(verified_matrix, location, 10)

    return {
        "verified_skills": len(verified),
        "total_resume_skills": len(skill_names),
        "verification_rate": round(len(verified) / max(len(skill_names), 1) * 100),
        "market_summary": market.get("summary", {}),
        "jobs": jobs.get("jobs", []),
        "resume_titles": cached.get("job_titles", []),
    }


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, summary="Clear resume data")
async def delete_resume(current_user: CurrentUser) -> None:
    await cache_delete(f"resume:{current_user.id}")