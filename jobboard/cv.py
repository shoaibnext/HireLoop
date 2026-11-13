"""Anonymous CV upload, explainable job matching, and expiry cleanup."""

import hashlib
import hmac
import io
import logging
import secrets
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .config import settings
from .employer import csrf_token, current_user, verify_csrf
from .models import Application, CVProfile, Job, MatchCache, get_db
from .rate_limit import limiter
from .skills import detect_level, extract_skills

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
logger = logging.getLogger("hireloop.cv")
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_EXTRACTED_CHARS = 250_000
MAX_PDF_PAGES = 100
CV_TTL = timedelta(days=30)


def _session_hash(request: Request, *, create: bool = False) -> str | None:
    token = request.session.get("cv_session_id")
    if not token and create:
        token = secrets.token_urlsafe(32)
        request.session["cv_session_id"] = token
    return hmac.new(settings.secret_key.encode(), token.encode(), hashlib.sha256).hexdigest() if token else None


def _profile(request: Request, db: Session) -> CVProfile | None:
    user = current_user(request, db)
    profile = None
    if user and user.role == "candidate":
        profile = db.query(CVProfile).filter_by(user_id=user.id).one_or_none()
    key = _session_hash(request)
    if profile is None and key:
        profile = db.query(CVProfile).filter_by(session_key_hash=key).one_or_none()
        if profile and user and user.role == "candidate" and profile.user_id is None:
            profile.user_id = user.id
            db.commit()
    if not profile:
        return None
    if profile and _as_utc(profile.expires_at) <= datetime.now(UTC):
        db.query(MatchCache).filter_by(cv_id=profile.id).delete(synchronize_session=False)
        db.delete(profile)
        db.commit()
        return None
    return profile


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _render(request: Request, name: str, **context):
    context.setdefault("noindex", True)
    context.setdefault("csrf_token", csrf_token(request))
    response = templates.TemplateResponse(request=request, name=name, context=context)
    response.headers["Cache-Control"] = "no-store"
    return response


def extract_pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    if len(reader.pages) > MAX_PDF_PAGES:
        raise ValueError("PDFs must contain 100 pages or fewer.")
    if reader.is_encrypted:
        raise ValueError("Password-protected PDFs are not supported.")
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def extract_docx_text(data: bytes) -> str:
    from docx import Document

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if "word/document.xml" not in archive.namelist() or sum(i.file_size for i in archive.infolist()) > 20_000_000:
            raise ValueError("This DOCX file is invalid or too large to process.")
    document = Document(io.BytesIO(data))
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    paragraphs.extend(cell.text for table in document.tables for row in table.rows for cell in row.cells)
    return "\n".join(paragraphs)


@router.get("/upload-cv", response_class=HTMLResponse)
def upload_page(request: Request, db: Session = Depends(get_db)):
    profile = _profile(request, db)
    preferences = request.session.get("cv_preferences", {})
    return _render(request, "upload_cv.html", profile=profile, error=None, message=None,
                   desired_location=preferences.get("location", ""),
                   remote_only=preferences.get("remote_only", False))


@router.post("/upload-cv", response_class=HTMLResponse)
@limiter.limit("5/hour")
async def upload_cv(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    desired_location = str(form.get("desired_location", "")).strip()[:100]
    remote_only = str(form.get("remote_only", "")) in {"1", "true", "on"}
    request.session["cv_preferences"] = {"location": desired_location, "remote_only": remote_only}
    upload = form.get("cv_file")
    text = ""
    has_upload = upload is not None and hasattr(upload, "filename") and bool(upload.filename)
    if has_upload:
        ext = Path(upload.filename).suffix.casefold()
        if ext not in {".pdf", ".docx"}:
            return _render(request, "upload_cv.html", profile=_profile(request, db), error="Choose a PDF or DOCX file.", message=None, desired_location=desired_location, remote_only=remote_only)
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            return _render(request, "upload_cv.html", profile=_profile(request, db), error="The file must be 5 MB or smaller.", message=None, desired_location=desired_location, remote_only=remote_only)
        try:
            if ext == ".pdf" and data.startswith(b"%PDF-"):
                text = extract_pdf_text(data)
            elif ext == ".docx" and data.startswith(b"PK"):
                text = extract_docx_text(data)
            else:
                raise ValueError("The file contents do not match the selected file type.")
        except (
            ValueError,
            OSError,
            zipfile.BadZipFile,
            KeyError,
            PdfReadError,
        ) as exc:
            logger.info("CV extraction rejected upload: %s", exc)
            message = str(exc) if isinstance(exc, ValueError) else "We couldn't read that file. Try a valid PDF or DOCX."
            return _render(request, "upload_cv.html", profile=_profile(request, db), error=message, message=None, desired_location=desired_location, remote_only=remote_only)
        text = text.strip()[:MAX_EXTRACTED_CHARS]
        if not text:
            return _render(request, "upload_cv.html", profile=_profile(request, db), error="No readable text was found in this file.", message=None, desired_location=desired_location, remote_only=remote_only)
        key = _session_hash(request, create=True)
        user = current_user(request, db)
        profile = None
        if user and user.role == "candidate":
            profile = db.query(CVProfile).filter_by(user_id=user.id).one_or_none()
        if profile is None:
            profile = db.query(CVProfile).filter_by(session_key_hash=key).one_or_none()
        now = datetime.now(UTC)
        if not profile:
            profile = CVProfile(
                session_key_hash=key,
                user_id=user.id if user and user.role == "candidate" else None,
                created_at=now,
            )
            db.add(profile)
        else:
            db.query(MatchCache).filter_by(cv_id=profile.id).delete(synchronize_session=False)
        profile.extracted_text = text
        profile.skills = ",".join(extract_skills(text))
        profile.expires_at = now + CV_TTL
        db.commit()
    else:
        existing_profile = _profile(request, db)
        if not existing_profile:
            return _render(request, "upload_cv.html", profile=None, error="Choose a CV file to get started.", message=None, desired_location=desired_location, remote_only=remote_only)
        db.query(MatchCache).filter_by(cv_id=existing_profile.id).delete(synchronize_session=False)
        db.commit()
    return RedirectResponse("/matches", status_code=303)


@router.post("/upload-cv/delete")
async def delete_cv(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    profile = _profile(request, db)
    if profile:
        db.query(MatchCache).filter_by(cv_id=profile.id).delete(synchronize_session=False)
        db.delete(profile)
        db.commit()
    request.session.pop("cv_session_id", None)
    request.session.pop("cv_preferences", None)
    request.session["cv_deleted"] = True
    return RedirectResponse("/upload-cv", status_code=303)


def calculate_match(cv_skills: list[str], job: Job, cv_level: str, preferences: dict) -> tuple[int, list[str], list[str], str]:
    required = [s.strip() for s in (job.skills or "").split(",") if s.strip()]
    matched = [skill for skill in required if skill in cv_skills]
    missing = [skill for skill in required if skill not in cv_skills]
    score = round(85 * len(matched) / len(required)) if required else 0
    boosts = []
    if cv_level != "unknown" and job.level == cv_level:
        score += 10
        boosts.append("experience level")
    location = preferences.get("location", "").casefold()
    location_fit = bool(location and location in (job.location or "").casefold())
    remote_fit = bool(preferences.get("remote_only") and job.is_remote)
    if location_fit or remote_fit:
        score += 5
        boosts.append("location/remote preference")
    why = f"Matches {len(matched)} of {len(required)} listed skills"
    if matched:
        why += f" ({', '.join(matched[:4])})"
    if boosts:
        why += "; also fits your " + " and ".join(boosts)
    return min(100, score), matched, missing, why + "."


def generate_claude_why(job_title: str, matched: list[str], missing: list[str]) -> str | None:
    if not settings.anthropic_api_key:
        return None
    prompt = f"Write one short, factual sentence about this job match using only these facts. Job: {job_title[:200]}. Matched skills: {', '.join(matched)}. Missing skills: {', '.join(missing)}. Do not invent experience or facts."
    try:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": settings.anthropic_api_key, "anthropic-version": "2023-06-01"},
            json={"model": settings.anthropic_model, "max_tokens": 80, "messages": [{"role": "user", "content": prompt}]},
            timeout=4.0,
        )
        response.raise_for_status()
        content = response.json().get("content", [])
        sentence = " ".join(block.get("text", "") for block in content if block.get("type") == "text").strip()
        return sentence[:500] or None
    except Exception:
        logger.exception("Claude match explanation unavailable")
        return None


@router.get("/matches", response_class=HTMLResponse)
def matches(request: Request, db: Session = Depends(get_db)):
    profile = _profile(request, db)
    if not profile:
        return _render(request, "matches.html", profile=None, matches=[])
    user = current_user(request, db)
    hidden_ids = set()
    if user:
        from .models import SeekerJobState

        hidden_ids = {
            row.job_id
            for row in db.query(SeekerJobState.job_id).filter_by(user_id=user.id, is_hidden=True).all()
        }
    preferences = request.session.get("cv_preferences", {})
    cv_skills = [s for s in (profile.skills or "").split(",") if s]
    cv_level = detect_level(profile.extracted_text)
    now = datetime.now(UTC)
    recent = db.query(Job).filter(
        Job.is_active.is_(True), or_(Job.source != "direct", Job.status == "approved"),
        or_(Job.expires_at.is_(None), Job.expires_at > now),
        ~Job.id.in_(hidden_ids) if hidden_ids else True,
    ).order_by(func.coalesce(Job.posted_at, Job.created_at).desc()).limit(200).all()
    rows = []
    fresh_rows = set()
    for job in recent:
        score, matched, missing, why = calculate_match(cv_skills, job, cv_level, preferences)
        cached = db.query(MatchCache).filter_by(cv_id=profile.id, job_id=job.id).one_or_none()
        if cached and (cached.score, cached.matched_skills, cached.missing_skills) == (score, ",".join(matched), ",".join(missing)):
            why = cached.why
        else:
            if not cached:
                cached = MatchCache(cv_id=profile.id, job_id=job.id)
                db.add(cached)
                fresh_rows.add(job.id)
            cached.score, cached.matched_skills, cached.missing_skills = score, ",".join(matched), ",".join(missing)
            cached.why, cached.computed_at = why, now
        rows.append((job, cached))
    db.commit()
    rows.sort(key=lambda pair: (-pair[1].score, -(_as_utc(pair[0].posted_at or pair[0].created_at).timestamp()), pair[0].id))
    top_rows = rows[:10]
    for job, cached in top_rows:
        if job.id in fresh_rows:
            explanation = generate_claude_why(
                job.title,
                [skill for skill in cached.matched_skills.split(",") if skill],
                [skill for skill in cached.missing_skills.split(",") if skill],
            )
            if explanation:
                cached.why = explanation
    db.commit()
    return _render(request, "matches.html", profile=profile, matches=top_rows)


@router.get("/account", response_class=HTMLResponse)
def candidate_account(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if user.role == "employer":
        return RedirectResponse("/employer/dashboard", status_code=303)
    profile = _profile(request, db)
    from .seeker import recommendations

    recommended_jobs, has_recommendation_inputs = recommendations(db, user)
    applications = (
        db.query(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .filter(func.lower(Application.candidate_email) == user.email.casefold())
        .order_by(Application.created_at.desc())
        .limit(20)
        .all()
    )
    return _render(
        request,
        "account_dashboard.html",
        user=user,
        profile=profile,
        applications=applications,
        recommendations=recommended_jobs,
        has_recommendation_inputs=has_recommendation_inputs,
    )


def cleanup_expired_cvs(db: Session) -> int:
    expired = db.query(CVProfile).filter(CVProfile.expires_at <= datetime.now(UTC)).all()
    ids = [profile.id for profile in expired]
    if ids:
        db.query(MatchCache).filter(MatchCache.cv_id.in_(ids)).delete(synchronize_session=False)
        db.query(CVProfile).filter(CVProfile.id.in_(ids)).delete(synchronize_session=False)
        db.commit()
    return len(ids)
