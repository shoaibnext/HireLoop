"""Server-rendered HireLoop MVP routes."""

import logging
import re
import secrets
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
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
from .content import get_blog_post, load_blog_posts
from .models import Base, CVProfile, Employer, Job, MatchCache, engine, get_db
from .rate_limit import limiter
from .skills import strip_html

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logger = logging.getLogger("hireloop")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
ET.register_namespace("", SITEMAP_NS)
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
    nonce = secrets.token_urlsafe(18)
    request.state.csp_nonce = nonce
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    csp = (
        "default-src 'self'; style-src 'self' https://cdn.tailwindcss.com "
        "https://fonts.googleapis.com 'unsafe-inline'; font-src https://fonts.gstatic.com; "
    )
    if settings.ads_enabled and settings.adsense_client and settings.adsense_slot:
        csp += (
            f"script-src 'nonce-{nonce}' 'unsafe-inline' 'unsafe-eval' "
            "'strict-dynamic' https: http: https://pagead2.googlesyndication.com; "
            "object-src 'none'; base-uri 'none'; "
            "connect-src 'self' https://pagead2.googlesyndication.com "
            "https://*.google.com https://*.doubleclick.net; "
            "img-src 'self' data: https://*.googlesyndication.com "
            "https://*.doubleclick.net https://*.google.com; "
            "frame-src https://*.doubleclick.net https://*.googlesyndication.com "
            "https://www.google.com"
        )
    else:
        csp += "script-src 'self' https://cdn.tailwindcss.com 'unsafe-inline'"
    response.headers.setdefault("Content-Security-Policy", csp)
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


def _ads_context() -> dict:
    return {
        "ads_enabled": bool(
            settings.ads_enabled and settings.adsense_client and settings.adsense_slot
        ),
        "adsense_client": settings.adsense_client,
        "adsense_slot": settings.adsense_slot,
    }


def _public_jobs(db: Session):
    now = datetime.now(UTC)
    return db.query(Job).filter(
        Job.is_active.is_(True),
        or_(Job.source != "direct", Job.status == "approved"),
        or_(Job.expires_at.is_(None), Job.expires_at > now),
    )


def _skill_job_query(db: Session, skill: str):
    return _public_jobs(db).filter(
        or_(
            Job.skills == skill,
            Job.skills.like(f"{skill},%"),
            Job.skills.like(f"%,{skill},%"),
            Job.skills.like(f"%,{skill}"),
        )
    )


def _landing_is_indexable(query) -> bool:
    return query.count() >= 10 and query.filter(Job.source == "direct").count() >= 1


LANDING_SKILLS = (
    "python", "javascript", "typescript", "java", "go", "sql",
    "postgresql", "react", "fastapi", "django", "docker",
    "kubernetes", "aws", "llm", "pytorch",
)
SKILL_LANDING_SLUGS = {slugify(skill): skill for skill in LANDING_SKILLS}
CITY_LANDING_PAGES = {
    "karachi": ("Karachi", "Find technology roles that explicitly list Karachi as a work location. Check each posting for office, hybrid, or remote expectations and application details."),
    "lahore": ("Lahore", "Review technology openings that name Lahore in their location details. Compare the stated work arrangement and required skills before applying."),
    "islamabad": ("Islamabad", "Browse roles that identify Islamabad as their work location. Read the employer's location and eligibility requirements in each listing."),
    "rawalpindi": ("Rawalpindi", "Explore jobs with Rawalpindi in the location field. Open each posting to confirm whether the role is office-based, hybrid, or remote."),
    "faisalabad": ("Faisalabad", "See technology roles whose listing names Faisalabad. Use the job details to check the expected work setup and application route."),
    "peshawar": ("Peshawar", "Browse technology opportunities that list Peshawar as a work location. Check the full posting for role scope and location eligibility."),
    "quetta": ("Quetta", "Review roles that explicitly mention Quetta in their location information. Confirm the employer's work arrangement and requirements in the listing."),
    "multan": ("Multan", "Find technology jobs that name Multan as their location. Compare the listed responsibilities, skills, and work arrangement for each role."),
}
SKILL_INTROS = {
    "python": "Compare current roles that name Python as a core skill. Review the frameworks, experience level, and work location in each listing.",
    "javascript": "Explore jobs that call for JavaScript across application and web development. Each listing provides its own stack and role expectations.",
    "typescript": "Browse roles listing TypeScript, and compare how each team describes its application stack and responsibilities.",
    "java": "Find openings that list Java among their skills. Read each role's requirements to understand the frameworks and systems involved.",
    "go": "Review current roles that identify Go or Golang as a skill. Check each posting for the product area and experience it describes.",
    "sql": "Explore technology jobs that include SQL in their normalized skills. Compare the data work and other tools listed by each employer.",
    "postgresql": "Browse roles listing PostgreSQL and review the database responsibilities, surrounding stack, and work arrangement in each post.",
    "react": "Compare openings that list React, from interface-focused work to broader application roles. The job descriptions explain each team's needs.",
    "fastapi": "Find roles that mention FastAPI and inspect the Python services, APIs, and responsibilities described in each listing.",
    "django": "Browse jobs naming Django and compare the application work and supporting technologies requested by each employer.",
    "docker": "Review openings that include Docker in their skills. Check whether each role focuses on development environments, deployment, or platform work.",
    "kubernetes": "Explore roles listing Kubernetes and read each post for the platform scope, operational expectations, and related tools.",
    "aws": "Compare opportunities that name AWS. Review the services, responsibilities, and experience requirements stated in each job post.",
    "llm": "Browse roles listing large language models or LLMs. Check each description for the application area, model tooling, and evaluation work involved.",
    "pytorch": "Find openings that include PyTorch and read the listing for its machine-learning tasks, team context, and other required skills.",
}


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
            "canonical_url": f"{settings.site_url}/" if not filtered else None,
            "meta_title": "Technology and AI jobs in Pakistan | HireLoop",
            "meta_description": "Search active technology and AI jobs in Pakistan and remote roles open to Pakistani developers.",
            "skill_landing_links": [
                (slug, SKILL_DISPLAY.get(skill, skill.title()), skill)
                for slug, skill in SKILL_LANDING_SLUGS.items()
            ] if not filtered else [],
            "city_landing_links": list(CITY_LANDING_PAGES.items()) if not filtered else [],
            **_ads_context(),
        },
    )


SKILL_DISPLAY = {
    "javascript": "JavaScript", "typescript": "TypeScript", "postgresql": "PostgreSQL",
    "llm": "LLM", "generative ai": "Generative AI", "rag": "RAG",
    "fastapi": "FastAPI", "pytorch": "PyTorch", "scikit-learn": "scikit-learn",
    "next.js": "Next.js", "node.js": "Node.js", "aws": "AWS", "gcp": "GCP",
}


def _breadcrumb_data(title: str, url: str) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "Home", "item": f"{settings.site_url}/"},
            {"@type": "ListItem", "position": 2, "name": "Jobs", "item": f"{settings.site_url}/"},
            {"@type": "ListItem", "position": 3, "name": title, "item": url},
        ],
    }


def _landing_response(
    request: Request,
    db: Session,
    query,
    *,
    title: str,
    intro: str,
    canonical_url: str,
    location: str = "",
    page: int = 1,
):
    total = query.count()
    direct_count = query.filter(Job.source == "direct").count()
    indexable = total >= 10 and direct_count >= 1
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
        for row in db.query(MatchCache)
        .filter_by(cv_id=profile.id)
        .filter(MatchCache.job_id.in_([job.id for job in jobs]))
        .all()
    } if profile and jobs else {}
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "jobs": jobs,
            "q": "",
            "location": location,
            "remote": False,
            "types": [],
            "levels": [],
            "has_salary": False,
            "page": page,
            "total": total,
            "pages": max(1, (total + 19) // 20),
            "noindex": not indexable,
            "featured_ids": {
                job.id for job in jobs
                if job.featured_until and _as_utc(job.featured_until) > datetime.now(UTC)
            },
            "contact_email": settings.contact_email,
            "cv_exists": profile is not None,
            "match_scores": match_scores,
            "landing_title": title,
            "landing_intro": intro,
            "landing_breadcrumb_ld": _breadcrumb_data(title, canonical_url),
            "form_action": "/",
            "meta_title": f"{title} jobs | HireLoop",
            "meta_description": intro,
            "canonical_url": canonical_url,
            **_ads_context(),
        },
    )


@app.get("/jobs/skill/{skill_slug}", response_class=HTMLResponse)
def skill_jobs(
    skill_slug: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    skill = SKILL_LANDING_SLUGS.get(skill_slug)
    if not skill:
        raise HTTPException(404)
    display = SKILL_DISPLAY.get(skill, skill.title())
    query = _skill_job_query(db, skill)
    canonical_url = f"{settings.site_url}/jobs/skill/{skill_slug}"
    intro = SKILL_INTROS[skill]
    return _landing_response(
        request, db, query, title=f"{display} jobs", intro=intro,
        canonical_url=canonical_url, page=page,
    )


@app.get("/jobs/city/{city_slug}", response_class=HTMLResponse)
def city_jobs(
    city_slug: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
):
    city_data = CITY_LANDING_PAGES.get(city_slug)
    if not city_data:
        raise HTTPException(404)
    city, intro = city_data
    query = _public_jobs(db).filter(Job.location.ilike(f"%{city}%"))
    canonical_url = f"{settings.site_url}/jobs/city/{city_slug}"
    return _landing_response(
        request, db, query, title=f"Technology jobs in {city}", intro=intro,
        canonical_url=canonical_url, location=city, page=page,
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
            "application_submitted": request.query_params.get("application") == "sent",
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


@app.get("/blog", response_class=HTMLResponse)
def blog_index(request: Request):
    description = "Practical guides for finding technology roles and evaluating job offers."
    return templates.TemplateResponse(
        request=request,
        name="blog_index.html",
        context={
            "posts": load_blog_posts(),
            "noindex": False,
            "canonical_url": f"{settings.site_url}/blog",
            "meta_title": "Career guides for technology roles | HireLoop",
            "meta_description": description,
            **_ads_context(),
        },
    )


@app.get("/blog/{slug}", response_class=HTMLResponse)
def blog_article(slug: str, request: Request):
    post = get_blog_post(slug)
    if not post:
        raise HTTPException(404)
    canonical_url = f"{settings.site_url}/blog/{post.slug}"
    return templates.TemplateResponse(
        request=request,
        name="blog_post.html",
        context={
            "post": post,
            "noindex": False,
            "canonical_url": canonical_url,
            "meta_title": f"{post.title} | HireLoop",
            "meta_description": post.description,
            **_ads_context(),
        },
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
    root = ET.Element(f"{{{SITEMAP_NS}}}urlset")

    def add_url(location: str, lastmod: date | None = None):
        item = ET.SubElement(root, f"{{{SITEMAP_NS}}}url")
        ET.SubElement(item, f"{{{SITEMAP_NS}}}loc").text = location
        if lastmod:
            ET.SubElement(item, f"{{{SITEMAP_NS}}}lastmod").text = lastmod.isoformat()

    add_url(f"{settings.site_url}/")
    for job in direct:
        add_url(f"{settings.site_url}/jobs/{job.id}-{slugify(job.title)}")
    add_url(f"{settings.site_url}/blog")
    posts = load_blog_posts()
    for post in posts:
        add_url(f"{settings.site_url}/blog/{post.slug}", post.updated)
    for slug, skill in SKILL_LANDING_SLUGS.items():
        if _landing_is_indexable(_skill_job_query(db, skill)):
            add_url(f"{settings.site_url}/jobs/skill/{slug}")
    for slug, (city, _intro) in CITY_LANDING_PAGES.items():
        query = _public_jobs(db).filter(Job.location.ilike(f"%{city}%"))
        if _landing_is_indexable(query):
            add_url(f"{settings.site_url}/jobs/city/{slug}")
    xml = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return Response(
        xml,
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
from .applications import router as applications_router
from .cv import router as cv_router
from .employer import router as employer_router

app.include_router(employer_router)
app.include_router(cv_router)
app.include_router(alerts_router)
app.include_router(applications_router)


def _cv_session_hash(request: Request) -> str | None:
    import hashlib
    import hmac

    token = request.session.get("cv_session_id")
    if not token:
        return None
    return hmac.new(settings.secret_key.encode(), token.encode(), hashlib.sha256).hexdigest()
