"""Private seeker job state, preferences, recommendations, and search history."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .employer import current_user, verify_csrf
from .models import (
    Application,
    CVProfile,
    Job,
    RecentSearch,
    SeekerJobState,
    SeekerPreference,
    User,
    get_db,
)
from .skills import extract_skills

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
APPLICATION_STATES = {"applied", "interviewing", "offer", "rejected"}
SEARCH_KEYS = ("q", "location", "remote", "type", "level", "has_salary", "posted", "sort")


def _public_job(db: Session, job_id: int) -> Job:
    job = db.get(Job, job_id)
    now = datetime.now(UTC)
    if (
        not job
        or not job.is_active
        or (job.source == "direct" and job.status != "approved")
        or (job.expires_at and _utc(job.expires_at) <= now)
    ):
        raise HTTPException(404)
    return job


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _candidate(request: Request, db: Session) -> User | None:
    user = current_user(request, db)
    if user and user.role != "candidate":
        raise HTTPException(403, "Seeker features are for candidate accounts")
    return user


def _state(db: Session, user_id: int, job_id: int) -> SeekerJobState:
    state = db.query(SeekerJobState).filter_by(user_id=user_id, job_id=job_id).one_or_none()
    if state is None:
        state = SeekerJobState(user_id=user_id, job_id=job_id)
        db.add(state)
        db.flush()
    return state


def _job_return_path(job_id: int) -> str:
    return f"/jobs/{int(job_id)}"


def remember_search(db: Session, user: User | None, params: dict[str, list[str]]) -> None:
    if not user or user.role != "candidate":
        return
    filtered = any(params.get(key) for key in SEARCH_KEYS)
    if not filtered:
        return
    query = urlencode([(key, value) for key in SEARCH_KEYS for value in params.get(key, [])])
    if not query:
        return
    existing = db.query(RecentSearch).filter_by(user_id=user.id, query_string=query).one_or_none()
    if existing:
        existing.searched_at = datetime.now(UTC)
    else:
        db.add(RecentSearch(user_id=user.id, query_string=query))
    db.flush()
    old = (
        db.query(RecentSearch)
        .filter_by(user_id=user.id)
        .order_by(RecentSearch.searched_at.desc(), RecentSearch.id.desc())
        .offset(5)
        .all()
    )
    for item in old:
        db.delete(item)


def recent_search_items(db: Session, user_id: int) -> list[dict[str, str]]:
    rows = (
        db.query(RecentSearch)
        .filter_by(user_id=user_id)
        .order_by(RecentSearch.searched_at.desc(), RecentSearch.id.desc())
        .limit(5)
        .all()
    )
    results = []
    for row in rows:
        label = row.query_string.replace("&", " · ").replace("=", ": ")
        results.append({"url": f"/?{row.query_string}", "label": label})
    return results


@router.post("/jobs/{job_id}/save")
async def toggle_saved_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    job = _public_job(db, job_id)
    user = _candidate(request, db)
    if not user:
        request.session["seeker_return_to"] = _job_return_path(job.id)
        return RedirectResponse("/login", status_code=303)
    state = _state(db, user.id, job.id)
    state.is_saved = not state.is_saved
    state.updated_at = datetime.now(UTC)
    db.commit()
    return RedirectResponse(_job_return_path(job.id), status_code=303)


@router.post("/jobs/{job_id}/hide")
async def hide_job(job_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    job = _public_job(db, job_id)
    user = _candidate(request, db)
    if not user:
        request.session["seeker_return_to"] = _job_return_path(job.id)
        return RedirectResponse("/login", status_code=303)
    state = _state(db, user.id, job.id)
    state.is_hidden = True
    state.updated_at = datetime.now(UTC)
    db.commit()
    return RedirectResponse("/my-jobs", status_code=303)


@router.post("/jobs/{job_id}/state")
async def update_job_state(job_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    job = _public_job(db, job_id)
    user = _candidate(request, db)
    if not user:
        request.session["seeker_return_to"] = _job_return_path(job.id)
        return RedirectResponse("/login", status_code=303)
    state = _state(db, user.id, job.id)
    action = str(form.get("action", ""))
    if action == "restore":
        state.is_hidden = False
    elif action == "remove":
        state.is_saved = False
        state.application_status = None
        state.private_note = ""
        if db.query(Application.id).filter_by(job_id=job.id, candidate_email=user.email).first():
            state.is_hidden = True
    else:
        status = "applied" if form.get("quick_apply") else str(form.get("status", "applied"))
        if status not in APPLICATION_STATES:
            raise HTTPException(400, "Choose a valid application status")
        note = str(form.get("note", "")).strip()
        if len(note) > 2000:
            raise HTTPException(400, "Private notes must be 2,000 characters or fewer")
        state.application_status = status
        state.private_note = note
        state.is_hidden = False
    state.updated_at = datetime.now(UTC)
    db.commit()
    return RedirectResponse("/my-jobs", status_code=303)


@router.get("/my-jobs", response_class=HTMLResponse)
def my_jobs(request: Request, tab: str = "saved", db: Session = Depends(get_db)):
    user = _candidate(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if tab not in {"saved", *APPLICATION_STATES}:
        tab = "saved"
    states = db.query(SeekerJobState).filter_by(user_id=user.id).all()
    state_by_job = {state.job_id: state for state in states}
    applications = (
        db.query(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .filter(
            func.lower(Application.candidate_email) == user.email.casefold(),
            Job.is_active.is_(True),
            or_(Job.source != "direct", Job.status == "approved"),
        )
        .all()
    )
    items = {"saved": [], "applied": [], "interviewing": [], "offer": [], "rejected": []}
    for state in states:
        job = db.get(Job, state.job_id)
        if not job or not job.is_active or (job.source == "direct" and job.status != "approved"):
            continue
        item = {"job": job, "state": state, "source_application": None}
        if state.is_saved and not state.is_hidden:
            items["saved"].append(item)
        if state.application_status and not state.is_hidden:
            items[state.application_status].append(item)
    for application, job in applications:
        state = state_by_job.get(job.id)
        if state and (state.is_hidden or state.application_status):
            continue
        status = state.application_status if state else "applied"
        items[status].append({"job": job, "state": state, "source_application": application})
    hidden = [
        {"job": db.get(Job, state.job_id), "state": state}
        for state in states
        if state.is_hidden and db.get(Job, state.job_id)
    ]
    counts = {key: len(value) for key, value in items.items()}
    return templates.TemplateResponse(
        request=request,
        name="my_jobs.html",
        context={"user": user, "tab": tab, "items": items[tab], "counts": counts, "tabs": list(items), "hidden": hidden, "csrf_token": _csrf(request)},
    )


@router.get("/preferences", response_class=HTMLResponse)
def preferences_page(request: Request, db: Session = Depends(get_db)):
    user = _candidate(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    preferences = db.query(SeekerPreference).filter_by(user_id=user.id).one_or_none()
    return templates.TemplateResponse(
        request=request,
        name="preferences.html",
        context={"user": user, "preferences": preferences, "csrf_token": _csrf(request), "saved": request.query_params.get("saved") == "1"},
    )


def _csrf(request: Request) -> str:
    from .employer import csrf_token

    return csrf_token(request)


@router.post("/preferences")
async def save_preferences(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    user = _candidate(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    roles = [part.strip()[:100] for part in str(form.get("target_roles", "")).splitlines() if part.strip()]
    skills = extract_skills(str(form.get("skills", ""))[:2000])
    location = str(form.get("location", "")).strip()[:100]
    currency = str(form.get("salary_currency", "").upper()) or None
    if currency not in {None, "PKR", "USD"}:
        raise HTTPException(400, "Currency must be PKR or USD")
    salary_raw = str(form.get("minimum_salary", "")).strip()
    try:
        minimum_salary = Decimal(salary_raw) if salary_raw else None
    except InvalidOperation as exc:
        raise HTTPException(400, "Enter a valid minimum salary") from exc
    if minimum_salary is not None and (minimum_salary < 0 or minimum_salary > Decimal(9999999999)):
        raise HTTPException(400, "Enter a valid minimum salary")
    if (minimum_salary is not None) != bool(currency):
        raise HTTPException(400, "Choose a currency with the minimum salary")
    preferences = db.query(SeekerPreference).filter_by(user_id=user.id).one_or_none()
    if preferences is None:
        preferences = SeekerPreference(user_id=user.id)
        db.add(preferences)
    preferences.target_roles = "\n".join(roles)[:1000]
    preferences.skills = ",".join(skills)
    preferences.location = location
    preferences.remote_only = str(form.get("remote_only", "")) in {"1", "true", "on"}
    preferences.minimum_salary = minimum_salary
    preferences.salary_currency = currency
    preferences.updated_at = datetime.now(UTC)
    db.commit()
    return RedirectResponse("/preferences?saved=1", status_code=303)


@router.post("/recent-searches/clear")
async def clear_recent_searches(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    user = _candidate(request, db)
    if not user:
        return RedirectResponse("/login", status_code=303)
    db.query(RecentSearch).filter_by(user_id=user.id).delete(synchronize_session=False)
    db.commit()
    return RedirectResponse("/", status_code=303)


def recommendations(db: Session, user: User) -> tuple[list[dict], bool]:
    profile = db.query(CVProfile).filter_by(user_id=user.id).one_or_none()
    preferences = db.query(SeekerPreference).filter_by(user_id=user.id).one_or_none()
    cv_skills = {value.strip() for value in (profile.skills.split(",") if profile else []) if value.strip()}
    pref_skills = {value.strip() for value in (preferences.skills.split(",") if preferences else []) if value.strip()}
    roles = [value.strip().casefold() for value in (preferences.target_roles.splitlines() if preferences else []) if value.strip()]
    has_preferences = bool(
        pref_skills or roles or (preferences and (preferences.location or preferences.remote_only or preferences.minimum_salary is not None))
    )
    if not cv_skills and not has_preferences:
        return [], False
    hidden_ids = {
        row[0] for row in db.query(SeekerJobState.job_id).filter_by(user_id=user.id, is_hidden=True).all()
    }
    now = datetime.now(UTC)
    query = db.query(Job).filter(
        Job.is_active.is_(True),
        or_(Job.source != "direct", Job.status == "approved"),
        or_(Job.expires_at.is_(None), Job.expires_at > now),
    )
    if hidden_ids:
        query = query.filter(~Job.id.in_(hidden_ids))
    scored = []
    for job in query.order_by(Job.posted_at.desc().nullslast(), Job.created_at.desc()).limit(500).all():
        job_skills = {value.strip() for value in (job.skills or "").split(",") if value.strip()}
        matched = sorted(job_skills & (cv_skills | pref_skills))
        role_match = any(role in job.title.casefold() for role in roles)
        location_match = bool(preferences and preferences.location and preferences.location.casefold() in (job.location or "").casefold())
        remote_match = bool(preferences and preferences.remote_only and job.is_remote)
        salary_match = bool(preferences and preferences.minimum_salary is not None and job.salary_currency == preferences.salary_currency and job.salary_min is not None and job.salary_min >= preferences.minimum_salary)
        overlap = len(matched)
        if overlap or role_match or location_match or remote_match or salary_match:
            ranked_score = overlap * 100 + int(role_match) * 20 + int(location_match) * 5 + int(remote_match) * 5 + int(salary_match) * 5
            scored.append((ranked_score, job, matched))
    scored.sort(key=lambda item: (-item[0], -_utc(item[1].posted_at or item[1].created_at).timestamp(), item[1].id))
    return [{"job": job, "matched_skills": matched} for _score, job, matched in scored[:10]], True
