"""Server-rendered HireLoop MVP routes."""

import logging
import re
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import nh3
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import case, func, or_, text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from .config import settings
from .models import Base, CVProfile, Employer, Job, MatchCache, engine, get_db
from .rate_limit import limiter
from .skills import strip_html

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("hireloop")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
ALLOWED_TAGS = {
    "p",
    "br",
    "ul",
    "ol",
    "li",
    "strong",
    "b",
    "em",
    "i",
    "h2",
    "h3",
    "h4",
    "blockquote",
    "code",
    "pre",
}


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "job"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # create_all is intentionally limited to test/dev; production schema is managed by Alembic.
    if settings.database_url.startswith("sqlite") and settings.database_url.endswith(
        ":memory:"
    ):
        Base.metadata.create_all(engine)
    yield


app = FastAPI(title="HireLoop", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=settings.site_url.startswith("https://"),
    session_cookie="hireloop_session",
    max_age=30 * 24 * 60 * 60,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self' https://cdn.tailwindcss.com https://fonts.googleapis.com 'unsafe-inline'; font-src https://fonts.gstatic.com; script-src 'self' https://cdn.tailwindcss.com 'unsafe-inline'",
    )
    return response


def job_filters(
    db: Session,
    q: str,
    location: str,
    remote: bool,
    types: list[str],
    levels: list[str],
    has_salary: bool,
):
    now = datetime.now(UTC)
    query = db.query(Job).filter(
        Job.is_active.is_(True),
        or_(Job.source != "direct", Job.status == "approved"),
        or_(Job.expires_at.is_(None), Job.expires_at > now),
    )
    if q:
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Job.title.ilike(term),
                Job.company.ilike(term),
                Job.tags.ilike(term),
                Job.description.ilike(term),
                Job.skills.ilike(term),
            )
        )
    if location:
        query = query.filter(Job.location.ilike(f"%{location.strip()}%"))
    if remote:
        query = query.filter(Job.is_remote.is_(True))
    if types:
        normalized = set(types)
        if "contract" in normalized:
            normalized.add("freelance")
        query = query.filter(Job.job_type.in_(normalized))
    if levels:
        recognized_levels = {"junior", "mid", "senior", "unknown"}
        query = query.filter(Job.level.in_(set(levels) & recognized_levels))
    if has_salary:
        query = query.filter(
            or_(
                Job.salary != "",
                Job.salary_min.is_not(None),
                Job.salary_max.is_not(None),
            )
        )
    return query


@app.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    q: str = "",
    location: str = "",
    remote: bool = False,
    types: list[str] = Query(default=[], alias="type"),
    levels: list[str] = Query(default=[], alias="level"),
    has_salary: bool = False,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    filtered = bool(q or location or remote or types or levels or has_salary)
    query = job_filters(db, q, location, remote, types, levels, has_salary)
    total = query.count()
    jobs = (
        query.order_by(
            case((Job.featured_until > datetime.now(UTC), 0), else_=1),
            func.coalesce(Job.posted_at, Job.created_at).desc(),
        )
        .offset((page - 1) * 20)
        .limit(20)
        .all()
    )
    cv_key = _cv_session_hash(request)
    profile = db.query(CVProfile).filter_by(session_key_hash=cv_key).one_or_none() if cv_key else None
    if profile and _as_utc(profile.expires_at) <= datetime.now(UTC):
        profile = None
    match_scores = {
        row.job_id: row.score
        for row in db.query(MatchCache).filter_by(cv_id=profile.id).filter(MatchCache.job_id.in_([job.id for job in jobs])).all()
    } if profile and jobs else {}
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "jobs": jobs,
            "q": q,
            "location": location,
            "remote": remote,
            "types": types,
            "levels": levels,
            "has_salary": has_salary,
            "page": page,
            "total": total,
            "pages": max(1, (total + 19) // 20),
            "noindex": filtered,
            "featured_ids": {
                job.id
                for job in jobs
                if job.featured_until
                and _as_utc(job.featured_until) > datetime.now(UTC)
            },
            "contact_email": settings.contact_email,
            "cv_exists": profile is not None,
            "match_scores": match_scores,
        },
    )


@app.get("/jobs/{job_ref}", response_class=HTMLResponse)
def job_detail(job_ref: str, request: Request, db: Session = Depends(get_db)):
    match = re.match(r"^(\d+)(?:-(.*))?$", job_ref)
    if not match:
        raise HTTPException(404)
    job = db.get(Job, int(match.group(1)))
    if not job:
        raise HTTPException(404)
    if not job.is_active:
        return HTMLResponse(
            "<!doctype html><title>Job no longer available</title><main><h1>Job no longer available</h1><p>This listing is no longer active. Browse current jobs to find another opportunity.</p><a href='/'>Browse jobs</a></main>",
            status_code=410,
        )
    if (
        job.source == "direct"
        and job.expires_at
        and _as_utc(job.expires_at) <= datetime.now(UTC)
    ):
        return HTMLResponse(
            "<!doctype html><title>Job no longer available</title><main><h1>Job no longer available</h1><p>This listing has expired. Browse current jobs to find another opportunity.</p><a href='/'>Browse jobs</a></main>",
            status_code=410,
        )
    if job.source == "direct" and (job.status != "approved"):
        raise HTTPException(404)
    canonical = f"/jobs/{job.id}-{slugify(job.title)}"
    if job_ref != f"{job.id}-{slugify(job.title)}":
        return RedirectResponse(canonical, status_code=301)
    safe_description = nh3.clean(
        job.description, tags=ALLOWED_TAGS, attributes={}, link_rel=None
    )
    cv_key = _cv_session_hash(request)
    profile = db.query(CVProfile).filter_by(session_key_hash=cv_key).one_or_none() if cv_key else None
    if profile and _as_utc(profile.expires_at) <= datetime.now(UTC):
        profile = None
    cached_match = db.query(MatchCache).filter_by(cv_id=profile.id, job_id=job.id).one_or_none() if profile else None
    return templates.TemplateResponse(
        request=request,
        name="job_detail.html",
        context={
            "job": job,
            "description": safe_description,
            "noindex": job.source != "direct",
            "job_posting_ld": job_posting_ld(db, job),
            "featured": bool(
                job.featured_until and _as_utc(job.featured_until) > datetime.now(UTC)
            ),
            "contact_email": settings.contact_email,
            "match_score": cached_match.score if cached_match else None,
        },
    )


@app.get("/{page_name:regex(about|contact|privacy)}", response_class=HTMLResponse)
def static_page(page_name: str, request: Request):
    pages = {
        "about": (
            "About HireLoop",
            "Find technology and AI roles in Pakistan and remote opportunities open to Pakistani developers.",
        ),
        "contact": ("Contact", f"Contact us at {settings.contact_email}."),
        "privacy": (
            "Privacy",
            "When you upload a CV, we store only extracted text and recognized skills, never the original file. CV data is linked to your signed anonymous session, is automatically deleted after 30 days, and can be deleted immediately with Delete my CV. We do not share it with employers unless you explicitly choose to apply. If optional match explanations are enabled, Anthropic receives only the job title and matched or missing skill names, never your CV text. Email alert addresses and preferences are used only to send the alerts you requested; every email includes an unsubscribe link that immediately deletes that subscription and its preferences.",
        ),
    }
    if page_name not in pages:
        raise HTTPException(404)
    title, body = pages[page_name]
    return templates.TemplateResponse(
        request=request,
        name="page.html",
        context={"title": title, "body": body, "noindex": False},
    )


@app.get("/sitemap.xml")
def sitemap(db: Session = Depends(get_db)):
    direct = (
        db.query(Job)
        .filter(
            Job.source == "direct",
            Job.is_active.is_(True),
            Job.status == "approved",
            or_(Job.expires_at.is_(None), Job.expires_at > datetime.now(UTC)),
        )
        .all()
    )
    urls = [f"<url><loc>{settings.site_url}/</loc></url>"]
    urls += [
        f"<url><loc>{settings.site_url}/jobs/{j.id}-{slugify(j.title)}</loc></url>"
        for j in direct
    ]
    return Response(
        '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + "".join(urls)
        + "</urlset>",
        media_type="application/xml",
    )


@app.get("/robots.txt")
def robots():
    return Response(
        f"User-agent: *\nAllow: /\nSitemap: {settings.site_url}/sitemap.xml\n",
        media_type="text/plain",
    )


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check database query failed")
        return JSONResponse(
            {"status": "unhealthy", "database": "unavailable"}, status_code=503
        )
    return {"status": "ok", "database": "ok"}


@app.get("/api/jobs")
def api_jobs(
    q: str = "", page: int = Query(default=1, ge=1), db: Session = Depends(get_db)
):
    query = job_filters(db, q, "", False, [], [], False)
    total = query.count()
    jobs = (
        query.order_by(
            case((Job.featured_until > datetime.now(UTC), 0), else_=1),
            func.coalesce(Job.posted_at, Job.created_at).desc(),
        )
        .offset((page - 1) * 20)
        .limit(20)
        .all()
    )
    return {
        "items": [
            {
                "id": j.id,
                "title": j.title,
                "company": j.company,
                "location": j.location,
                "source": j.source,
            }
            for j in jobs
        ],
        "page": page,
        "total": total,
    }


@app.exception_handler(404)
async def not_found(_request: Request, _exc):
    return HTMLResponse(
        "<!doctype html><title>Not found</title><h1>Page not found</h1><a href='/'>Back to jobs</a>",
        status_code=404,
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def job_posting_ld(db: Session, job: Job) -> dict | None:
    """Build JSON-LD only for approved direct jobs."""
    if job.source != "direct" or not job.is_active or job.status != "approved":
        return None
    posted = _as_utc(job.posted_at or job.created_at)
    data = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": job.title,
        "description": strip_html(job.description),
        "datePosted": posted.isoformat().replace("+00:00", "Z"),
        "validThrough": _as_utc(job.expires_at).isoformat().replace("+00:00", "Z")
        if job.expires_at
        else None,
        "employmentType": {
            "full-time": "FULL_TIME",
            "part-time": "PART_TIME",
            "contract": "CONTRACTOR",
            "freelance": "CONTRACTOR",
            "internship": "INTERN",
        }.get(job.job_type, "OTHER"),
        "hiringOrganization": {"@type": "Organization", "name": job.company},
    }
    if job.is_remote:
        data["jobLocationType"] = "TELECOMMUTE"
        data["applicantLocationRequirements"] = {"@type": "Country", "name": "Pakistan"}
    elif job.location:
        data["jobLocation"] = {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": job.location,
                "addressCountry": "PK",
            },
        }
    if job.salary_currency and (
        job.salary_min is not None or job.salary_max is not None
    ):
        salary_value = {"@type": "QuantitativeValue", "unitText": "MONTH"}
        if job.salary_min is not None:
            salary_value["minValue"] = float(job.salary_min)
        if job.salary_max is not None:
            salary_value["maxValue"] = float(job.salary_max)
        data["baseSalary"] = {
            "@type": "MonetaryAmount",
            "currency": job.salary_currency,
            "value": salary_value,
        }
    if job.employer_id:
        employer = db.get(Employer, job.employer_id)
        if employer and employer.website:
            data["hiringOrganization"]["sameAs"] = employer.website
    return data


from .alerts import router as alerts_router
from .cv import router as cv_router
from .employer import router as employer_router

app.include_router(employer_router)
app.include_router(cv_router)
app.include_router(alerts_router)


def _cv_session_hash(request: Request) -> str | None:
    import hashlib
    import hmac

    token = request.session.get("cv_session_id")
    if not token:
        return None
    return hmac.new(settings.secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()
