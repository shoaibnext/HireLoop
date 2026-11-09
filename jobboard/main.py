"""Server-rendered HireLoop MVP routes."""

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

import nh3
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from .config import settings
from .models import Base, Job, SessionLocal, engine

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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
    query = db.query(Job)
    if q:
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Job.title.ilike(term),
                Job.company.ilike(term),
                Job.tags.ilike(term),
                Job.description.ilike(term),
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
        clauses = []
        for level in levels:
            words = {
                "junior": ["junior", "jr", "entry"],
                "mid": ["mid", "intermediate"],
                "senior": ["senior", "sr", "lead"],
            }.get(level, [level])
            clauses.extend(Job.title.ilike(f"%{word}%") for word in words)
        if clauses:
            query = query.filter(or_(*clauses))
    if has_salary:
        query = query.filter(Job.salary != "")
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
        query.order_by(func.coalesce(Job.posted_at, Job.created_at).desc())
        .offset((page - 1) * 20)
        .limit(20)
        .all()
    )
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
    canonical = f"/jobs/{job.id}-{slugify(job.title)}"
    if job_ref != f"{job.id}-{slugify(job.title)}":
        return RedirectResponse(canonical, status_code=301)
    safe_description = nh3.clean(
        job.description, tags=ALLOWED_TAGS, attributes={}, link_rel=None
    )
    return templates.TemplateResponse(
        request=request,
        name="job_detail.html",
        context={
            "job": job,
            "description": safe_description,
            "noindex": job.source != "direct",
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
            "We collect only the information needed to operate HireLoop. Contact us with privacy requests.",
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


@app.get("/upload-cv", response_class=HTMLResponse)
@app.get("/post-job", response_class=HTMLResponse)
def coming_soon(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="page.html",
        context={
            "title": "Coming soon",
            "body": "This feature is coming soon.",
            "noindex": True,
        },
    )


@app.get("/sitemap.xml")
def sitemap(db: Session = Depends(get_db)):
    direct = db.query(Job).filter(Job.source == "direct").all()
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
        query.order_by(func.coalesce(Job.posted_at, Job.created_at).desc())
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
