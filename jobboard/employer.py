"""Employer accounts, direct job posting, and admin moderation routes."""

import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import nh3
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from passlib.context import CryptContext
from sqlalchemy import case, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .ingest import JOB_HTML_TAGS
from .models import Application, Employer, Job, User, get_db
from .rate_limit import limiter
from .skills import detect_level, extract_skills, strip_html

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
passwords = CryptContext(schemes=["bcrypt"], deprecated="auto")
JOB_TYPES = {"full-time", "part-time", "contract", "freelance", "internship"}
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, submitted: str | None) -> None:
    expected = request.session.get("csrf_token", "")
    if not submitted or not expected or not hmac.compare_digest(expected, submitted):
        raise HTTPException(
            status_code=403, detail="Your form expired. Reload and try again."
        )


def current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    return db.get(User, user_id) if user_id else None


def is_admin(user: User) -> bool:
    return user.email.casefold() in settings.admin_emails


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def employer_for(db: Session, user: User) -> Employer:
    employer = db.query(Employer).filter_by(user_id=user.id).one_or_none()
    if not employer:
        raise HTTPException(status_code=403, detail="Employer profile required")
    return employer


def expire_direct_jobs(db: Session) -> None:
    now = datetime.now(UTC)
    db.query(Job).filter(
        Job.source == "direct",
        Job.status.in_(("pending", "approved")),
        Job.expires_at.is_not(None),
        Job.expires_at <= now,
    ).update({Job.status: "expired", Job.is_active: False}, synchronize_session=False)
    db.query(Job).filter(
        Job.is_paid.is_(True),
        Job.featured_until.is_not(None),
        Job.featured_until <= now,
    ).update({Job.is_paid: False}, synchronize_session=False)
    db.commit()


def require_user(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    return user


def _page(request: Request, name: str, **context):
    context.setdefault("noindex", True)
    context.setdefault("csrf_token", csrf_token(request))
    return templates.TemplateResponse(request=request, name=name, context=context)


def _valid_web_url(value: str) -> bool:
    if not value:
        return True
    parsed = urlsplit(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _render_job_form(
    request: Request, *, company: str, job: Job | None = None, errors=None, values=None
):
    values = values or {}
    return _page(
        request,
        "job_form.html",
        title="Edit job" if job else "Post a job",
        action=f"/employer/jobs/{job.id}/edit" if job else "/post-job",
        company=company,
        job=job,
        errors=errors or {},
        values=values,
        pricing=settings.featured_price_pkr,
        payment_instructions=settings.payment_instructions,
    )


async def _validate_job_form(
    request: Request, employer: Employer, job: Job | None = None
):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    values = {
        key: str(form.get(key, "")).strip()
        for key in (
            "title",
            "location",
            "job_type",
            "description",
            "apply_url",
            "apply_email",
            "salary_min",
            "salary_max",
            "salary_currency",
        )
    }
    values["remote"] = "1" if form.get("remote") in {"1", "true", "on"} else ""
    values["featured_requested"] = (
        "1" if form.get("featured_requested") in {"1", "true", "on"} else ""
    )
    errors: dict[str, str] = {}
    title = values["title"]
    location = values["location"]
    description = values["description"]
    if not title or len(title) > 255:
        errors["title"] = "Enter a job title under 255 characters."
    if not location or len(location) > 255:
        errors["location"] = "Enter a location under 255 characters."
    if values["job_type"] not in JOB_TYPES:
        errors["job_type"] = "Choose a valid employment type."
    if not description or len(description) > 50_000:
        errors["description"] = "Enter a job description under 50,000 characters."
    if not values["apply_url"] and not values["apply_email"]:
        errors["apply"] = "Add an application URL or email address."
    if values["apply_url"] and not _valid_web_url(values["apply_url"]):
        errors["apply_url"] = "Use a complete http or https application URL."
    if values["apply_email"] and not EMAIL_RE.fullmatch(values["apply_email"]):
        errors["apply_email"] = "Enter a valid application email address."

    salary_min = salary_max = None
    currency = values["salary_currency"] or None
    try:
        if values["salary_min"]:
            salary_min = Decimal(values["salary_min"])
        if values["salary_max"]:
            salary_max = Decimal(values["salary_max"])
        if any(
            value is not None and (not value.is_finite() or value < 0)
            for value in (salary_min, salary_max)
        ):
            raise InvalidOperation
    except InvalidOperation:
        errors["salary"] = "Salary must be a non-negative number."
    if (salary_min is not None or salary_max is not None) and currency not in {
        "PKR",
        "USD",
    }:
        errors["salary_currency"] = "Choose PKR or USD for the salary."
    if salary_min is not None and salary_min > Decimal("9999999999.99"):
        errors["salary"] = "Salary is above the supported maximum."
    if salary_max is not None and salary_max > Decimal("9999999999.99"):
        errors["salary"] = "Salary is above the supported maximum."
    if salary_min is not None and salary_max is not None and salary_min > salary_max:
        errors["salary"] = "Minimum salary must not exceed maximum salary."
    if errors:
        return None, errors, values

    safe_description = nh3.clean(
        description, tags=JOB_HTML_TAGS, attributes={}, link_rel=None
    )
    if not strip_html(safe_description).strip():
        return (
            None,
            {"description": "Enter a job description with readable text."},
            values,
        )
    now = datetime.now(UTC)
    fields = {
        "title": title,
        "company": employer.company_name,
        "location": location,
        "is_remote": bool(values["remote"]),
        "job_type": values["job_type"],
        "salary": "",
        "tags": "",
        "description": safe_description,
        "source_url": "",
        "posted_at": job.posted_at if job else now,
        "last_seen_at": now,
        "is_active": True,
        "level": detect_level(title),
        "skills": ",".join(extract_skills(title, safe_description)),
        "apply_url": values["apply_url"] or None,
        "apply_email": values["apply_email"] or None,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": currency
        if salary_min is not None or salary_max is not None
        else None,
        "featured_requested": bool(values["featured_requested"]),
    }
    return fields, {}, values


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    return _page(request, "signup.html", errors={}, values={})


@router.post("/signup", response_class=HTMLResponse)
@limiter.limit("10/hour")
async def signup(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    email = str(form.get("email", "")).strip().casefold()
    password = str(form.get("password", ""))
    company_name = str(form.get("company_name", "")).strip()
    website = str(form.get("website", "")).strip()
    errors = {}
    if not EMAIL_RE.fullmatch(email):
        errors["email"] = "Enter a valid email address."
    if len(password) < 10 or len(password.encode("utf-8")) > 72:
        errors["password"] = "Use a password between 10 and 72 bytes."
    if not company_name or len(company_name) > 255:
        errors["company_name"] = "Enter a company name under 255 characters."
    if website and (len(website) > 2048 or not _valid_web_url(website)):
        errors["website"] = "Use a complete http or https website URL."
    values = {"email": email, "company_name": company_name, "website": website}
    if not errors and db.query(User.id).filter(func_lower(User.email) == email).first():
        errors["email"] = "An account with this email already exists."
    if errors:
        return _page(request, "signup.html", errors=errors, values=values)
    user = User(email=email, password_hash=passwords.hash(password), role="employer")
    db.add(user)
    try:
        db.flush()
        db.add(Employer(user_id=user.id, company_name=company_name, website=website))
        db.commit()
    except IntegrityError:
        db.rollback()
        return _page(
            request,
            "signup.html",
            errors={"email": "An account with this email already exists."},
            values=values,
        )
    request.session.clear()
    request.session["user_id"] = user.id
    csrf_token(request)
    return RedirectResponse("/employer/dashboard", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return _page(request, "login.html", error="")


@router.post("/login", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def login(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    email = str(form.get("email", "")).strip().casefold()
    password = str(form.get("password", ""))
    user = db.query(User).filter(func_lower(User.email) == email).one_or_none()
    if not user or not passwords.verify(password, user.password_hash):
        return _page(request, "login.html", error="Email or password is incorrect.")
    request.session.clear()
    request.session["user_id"] = user.id
    csrf_token(request)
    return RedirectResponse("/employer/dashboard", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@router.get("/post-job", response_class=HTMLResponse)
def post_job_page(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    employer = employer_for(db, user)
    return _render_job_form(request, company=employer.company_name)


@router.post("/post-job", response_class=HTMLResponse)
@limiter.limit("5/hour")
async def post_job(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    user = require_user(request, db)
    employer = employer_for(db, user)
    fields, errors, values = await _validate_job_form(request, employer)
    if errors:
        return _render_job_form(
            request, company=employer.company_name, errors=errors, values=values
        )
    job = Job(
        source="direct",
        external_id=f"direct-{uuid4().hex}",
        employer_id=employer.id,
        status="pending",
        is_paid=False,
        expires_at=datetime.now(UTC) + timedelta(days=30),
        **fields,
    )
    db.add(job)
    db.commit()
    return RedirectResponse("/employer/dashboard?posted=1", status_code=303)


@router.get("/employer/dashboard", response_class=HTMLResponse)
def employer_dashboard(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    expire_direct_jobs(db)
    employer = employer_for(db, user)
    jobs = (
        db.query(Job)
        .filter(Job.employer_id == employer.id)
        .order_by(Job.created_at.desc())
        .all()
    )
    application_count = (
        db.query(Application).filter_by(employer_id=employer.id).count()
    )
    return _page(
        request,
        "employer_dashboard.html",
        employer=employer,
        jobs=jobs,
        posted=request.query_params.get("posted") == "1",
        payment_instructions=settings.payment_instructions,
        pricing=settings.featured_price_pkr,
        is_admin=is_admin(user),
        application_count=application_count,
    )


@router.get("/employer/jobs/{job_id}/edit", response_class=HTMLResponse)
def edit_job_page(job_id: int, request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    employer = employer_for(db, user)
    job = db.get(Job, job_id)
    if not job or job.employer_id != employer.id:
        raise HTTPException(status_code=403, detail="You cannot edit this job")
    return _render_job_form(request, company=employer.company_name, job=job)


@router.post("/employer/jobs/{job_id}/edit", response_class=HTMLResponse)
@limiter.limit("5/hour")
async def edit_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    user = require_user(request, db)
    employer = employer_for(db, user)
    job = db.get(Job, job_id)
    if not job or job.employer_id != employer.id:
        raise HTTPException(status_code=403, detail="You cannot edit this job")
    fields, errors, values = await _validate_job_form(request, employer, job)
    if errors:
        return _render_job_form(
            request,
            company=employer.company_name,
            job=job,
            errors=errors,
            values=values,
        )
    for key, value in fields.items():
        setattr(job, key, value)
    db.commit()
    return RedirectResponse("/employer/dashboard?updated=1", status_code=303)


@router.post("/employer/jobs/{job_id}/close")
@limiter.limit("10/hour")
async def close_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    user = require_user(request, db)
    employer = employer_for(db, user)
    job = db.get(Job, job_id)
    if not job or job.employer_id != employer.id:
        raise HTTPException(status_code=403, detail="You cannot close this job")
    job.status = "closed"
    job.is_active = False
    db.commit()
    return RedirectResponse("/employer/dashboard", status_code=303)


@router.post("/employer/jobs/{job_id}/renew")
@limiter.limit("5/hour")
async def renew_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    user = require_user(request, db)
    employer = employer_for(db, user)
    job = db.get(Job, job_id)
    if not job or job.employer_id != employer.id:
        raise HTTPException(status_code=403, detail="You cannot renew this job")
    if job.status not in {"closed", "expired"}:
        raise HTTPException(
            status_code=400, detail="Only closed or expired jobs can be renewed"
        )
    job.status = "pending"
    job.is_active = True
    job.expires_at = datetime.now(UTC) + timedelta(days=30)
    db.commit()
    return RedirectResponse("/employer/dashboard", status_code=303)


@router.get("/admin/jobs", response_class=HTMLResponse)
def admin_jobs_page(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    expire_direct_jobs(db)
    jobs = (
        db.query(Job)
        .filter(Job.source == "direct")
        .order_by(Job.created_at.desc())
        .all()
    )
    return _page(
        request,
        "admin_jobs.html",
        jobs=jobs,
        active_featured_ids={
            job.id
            for job in jobs
            if job.featured_until and _as_utc(job.featured_until) > datetime.now(UTC)
        },
    )


@router.get("/admin/stats", response_class=HTMLResponse)
def admin_stats_page(request: Request, db: Session = Depends(get_db)):
    user = current_user(request, db)
    if not user or not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")

    since = datetime.now(UTC) - timedelta(days=30)
    jobs_by_source = [
        {"source": source, "total": total, "active": active or 0}
        for source, total, active in db.query(
            Job.source,
            func.count(Job.id),
            func.sum(case((Job.is_active.is_(True), 1), else_=0)),
        )
        .group_by(Job.source)
        .order_by(Job.source)
        .all()
    ]
    employer_total = db.query(func.count(Employer.id)).scalar() or 0
    employers_30d = (
        db.query(func.count(Employer.id))
        .filter(Employer.created_at >= since)
        .scalar()
        or 0
    )
    application_total = db.query(func.count(Application.id)).scalar() or 0
    applications_30d = (
        db.query(func.count(Application.id))
        .filter(Application.created_at >= since)
        .scalar()
        or 0
    )
    return _page(
        request,
        "admin_stats.html",
        jobs_by_source=jobs_by_source,
        employer_total=employer_total,
        employers_30d=employers_30d,
        application_total=application_total,
        applications_30d=applications_30d,
    )
@router.post("/admin/jobs/{job_id}/moderate")
@limiter.limit("30/minute")
async def moderate_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    user = require_user(request, db)
    if not is_admin(user):
        raise HTTPException(status_code=403, detail="Admin access required")
    job = db.get(Job, job_id)
    if not job or job.source != "direct":
        raise HTTPException(status_code=404, detail="Direct job not found")
    action = str(form.get("action", ""))
    now = datetime.now(UTC)
    if action == "approve":
        job.status = "approved"
        job.is_active = True
        if job.expires_at and _as_utc(job.expires_at) <= now:
            job.expires_at = now + timedelta(days=30)
    elif action == "reject":
        job.status = "closed"
        job.is_active = False
        job.moderation_note = str(form.get("moderation_note", "Rejected by admin"))[
            :2000
        ]
    elif action == "feature":
        featured = job.featured_until and _as_utc(job.featured_until) > now
        if featured:
            job.featured_until = None
            job.is_paid = False
        elif job.featured_until and _as_utc(job.featured_until) <= now:
            job.featured_until = None
            job.is_paid = False
            db.commit()
            raise HTTPException(
                status_code=400,
                detail="The previous featured period ended. Record the new payment first.",
            )
        elif not job.is_paid:
            raise HTTPException(
                status_code=400, detail="Mark the featured payment as paid first"
            )
        else:
            job.featured_until = now + timedelta(days=30)
    elif action == "mark_paid":
        note = str(form.get("payment_note", "")).strip()
        if not note:
            raise HTTPException(
                status_code=400, detail="Add a note for the manual payment"
            )
        job.is_paid = True
        job.payment_note = note[:2000]
    else:
        raise HTTPException(status_code=400, detail="Unknown moderation action")
    db.commit()
    return RedirectResponse("/admin/jobs", status_code=303)


@router.get("/pricing", response_class=HTMLResponse)
def pricing_page(request: Request):
    return _page(
        request,
        "pricing.html",
        featured_price=settings.featured_price_pkr,
        contact_email=settings.contact_email,
    )


def func_lower(column):
    """Case-insensitive email equality using SQL's lower() on either backend."""
    from sqlalchemy import func

    return func.lower(column)
