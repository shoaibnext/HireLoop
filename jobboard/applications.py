"""Seeker applications and employer-owned applicant review."""

import re
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .cv import _profile, calculate_match
from .employer import _page, current_user, employer_for, verify_csrf
from .models import Application, Employer, Job, User, get_db
from .rate_limit import limiter
from .skills import detect_level

router = APIRouter()
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
APPLICATION_STATUSES = {"new", "shortlisted", "rejected"}


def applicable_job(db: Session, job_id: int) -> Job | None:
    job = db.get(Job, job_id)
    if not job or job.source != "direct" or not job.is_active or job.status != "approved":
        return None
    if job.expires_at and _as_utc(job.expires_at) <= datetime.now(UTC):
        return None
    return job


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _render_apply(
    request: Request,
    job: Job,
    *,
    profile,
    values: dict | None = None,
    error: str | None = None,
    status_code: int = 200,
):
    response = _page(
        request,
        "apply.html",
        job=job,
        profile=profile,
        values=values or {},
        error=error,
    )
    response.status_code = status_code
    response.headers["Cache-Control"] = "no-store"
    return response


def _match_data(request: Request, profile, job: Job) -> tuple[int, list[str], list[str]]:
    preferences = request.session.get("cv_preferences", {})
    score, matched, missing, _why = calculate_match(
        [skill for skill in (profile.skills or "").split(",") if skill],
        job,
        detect_level(profile.extracted_text),
        preferences,
    )
    return score, matched, missing


@router.get("/jobs/{job_id}/apply", response_class=HTMLResponse)
def apply_page(job_id: int, request: Request, db: Session = Depends(get_db)):
    job = applicable_job(db, job_id)
    if not job:
        raise HTTPException(404)
    profile = _profile(request, db)
    return _render_apply(request, job, profile=profile)


@router.post("/jobs/{job_id}/apply", response_class=HTMLResponse)
@limiter.limit("5/hour")
async def submit_application(job_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    job = applicable_job(db, job_id)
    if not job:
        raise HTTPException(404)
    profile = _profile(request, db)
    values = {
        "email": str(form.get("email", "")).strip().casefold(),
        "note": str(form.get("note", "")).strip(),
    }
    if not profile:
        return _render_apply(
            request, job, profile=None, values=values,
            error="Upload a CV before applying. Only recognized skills will be shared.",
        )
    if not EMAIL_RE.fullmatch(values["email"]) or len(values["email"]) > 320:
        return _render_apply(request, job, profile=profile, values=values, error="Enter a valid email address.")
    if not values["note"] or len(values["note"]) > 1000:
        return _render_apply(request, job, profile=profile, values=values, error="Add a short note of up to 1,000 characters.")
    if form.get("share_skills") not in {"1", "true", "on"}:
        return _render_apply(request, job, profile=profile, values=values, error="Confirm that you want to share your email, note, and recognized skills with this employer.")
    duplicate = db.query(Application).filter_by(job_id=job.id, candidate_email=values["email"]).one_or_none()
    if duplicate:
        return _render_apply(request, job, profile=profile, values=values, error="You have already applied to this job.", status_code=409)

    score, matched, missing = _match_data(request, profile, job)
    employer_id = job.employer_id
    if not employer_id:
        raise HTTPException(409, "This job is not linked to an employer account.")
    application = Application(
        job_id=job.id,
        employer_id=employer_id,
        candidate_email=values["email"],
        candidate_note=values["note"],
        shared_skills=profile.skills or "",
        match_score=score,
        matched_skills=",".join(matched),
        missing_skills=",".join(missing),
        status="new",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.add(application)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _render_apply(request, job, profile=profile, values=values, error="You have already applied to this job.", status_code=409)
    slug = re.sub(r"[^a-z0-9]+", "-", job.title.casefold()).strip("-") or "job"
    return RedirectResponse(f"/jobs/{job.id}-{slug}?application=sent", status_code=303)


def require_employer(request: Request, db: Session) -> tuple[User, Employer]:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    return user, employer_for(db, user)


@router.get("/employer/applications", response_class=HTMLResponse)
def employer_applications(request: Request, db: Session = Depends(get_db)):
    _user, employer = require_employer(request, db)
    records = (
        db.query(Application, Job)
        .join(Job, Application.job_id == Job.id)
        .filter(Application.employer_id == employer.id)
        .order_by(Application.match_score.desc(), Application.created_at.asc(), Application.id.asc())
        .all()
    )
    rows = [
        {
            "application": application,
            "job": job,
            "reply_url": f"mailto:{application.candidate_email}?subject={quote(f'Re: {job.title}')}",
        }
        for application, job in records
    ]
    return _page(request, "applications.html", employer=employer, applications=rows)


@router.post("/employer/applications/{application_id}/status")
async def update_application_status(
    application_id: int, request: Request, db: Session = Depends(get_db)
):
    _user, employer = require_employer(request, db)
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    status = str(form.get("status", ""))
    if status not in APPLICATION_STATUSES:
        raise HTTPException(400, "Choose a valid application status.")
    application = db.get(Application, application_id)
    if not application:
        raise HTTPException(404)
    if application.employer_id != employer.id:
        raise HTTPException(403, "You cannot update this application.")
    application.status = status
    application.updated_at = datetime.now(UTC)
    db.commit()
    return RedirectResponse("/employer/applications", status_code=303)
