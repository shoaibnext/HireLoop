"""Double opt-in email alerts and preference management."""

import hashlib
import hmac
import html
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .config import settings
from .employer import csrf_token, verify_csrf
from .models import AlertSubscription, Job, get_db
from .rate_limit import limiter
from .skills import extract_skills

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
logger = logging.getLogger("hireloop.alerts")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
ALLOWED_WORK_MODES = {"any", "remote", "local"}
CONFIRM_TTL = timedelta(hours=48)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _render(request: Request, *, message: str, error: str | None = None, **context):
    response = templates.TemplateResponse(
        request=request,
        name="alerts.html",
        context={
            "noindex": True,
            "csrf_token": csrf_token(request),
            "message": message,
            "error": error,
            "values": {},
            "email_delivery_verified": settings.email_delivery_verified,
            **context,
        },
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _manage_url(token: str) -> str:
    return f"{settings.site_url}/alerts/preferences/{quote(token, safe='')}"


def _unsubscribe_url(token: str) -> str:
    return f"{settings.site_url}/alerts/unsubscribe/{quote(token, safe='')}"


def send_email(
    recipient: str,
    subject: str,
    html_body: str,
    *,
    idempotency_key: str,
    unsubscribe_url: str,
) -> None:
    """Send through Resend's HTTP API; the provider is optional until configured."""
    if not settings.email_api_key or not settings.email_from:
        raise RuntimeError("Email delivery is not configured")
    response = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {settings.email_api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key[:256],
        },
        json={
            "from": settings.email_from,
            "to": [recipient],
            "subject": subject,
            "html": html_body,
            "headers": {
                "List-Unsubscribe": f"<{unsubscribe_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            },
        },
        timeout=10,
    )
    response.raise_for_status()


def _send_confirmation(subscription: AlertSubscription, confirm_token: str, unsubscribe_token: str):
    confirm_url = f"{settings.site_url}/alerts/confirm/{quote(confirm_token, safe='')}"
    unsubscribe_url = _unsubscribe_url(unsubscribe_token)
    body = (
        "<p>Confirm your HireLoop job alerts by opening this link:</p>"
        f'<p><a href="{html.escape(confirm_url, quote=True)}">Confirm email alerts</a></p>'
        f'<p><a href="{html.escape(unsubscribe_url, quote=True)}">Unsubscribe</a></p>'
    )
    send_email(
        subscription.email,
        "Confirm your HireLoop job alerts",
        body,
        idempotency_key=f"alert-confirm-{subscription.id}-{_token_hash(confirm_token)}",
        unsubscribe_url=unsubscribe_url,
    )


@router.get("/alerts", response_class=HTMLResponse)
def alerts_page(request: Request):
    confirmed = request.query_params.get("confirmed") == "1"
    if not settings.email_delivery_verified:
        message = "Email alerts coming soon."
    elif confirmed:
        message = "Your email is confirmed. You will receive daily alerts when new jobs match your preferences."
    else:
        message = "Get a daily email when new technology jobs match your skills."
    return _render(request, message=message)


@router.post("/alerts", response_class=HTMLResponse)
@limiter.limit("3/hour")
async def subscribe(request: Request, db: Session = Depends(get_db)):
    if not settings.email_delivery_verified:
        return _render(request, message="Email alerts coming soon.")
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    email = str(form.get("email", "")).strip().casefold()
    skill_text = str(form.get("skills", ""))[:2000]
    skills = extract_skills(skill_text)
    location = str(form.get("location", "")).strip()[:100]
    work_mode = str(form.get("work_mode", "any"))
    if not EMAIL_RE.fullmatch(email) or len(email) > 320:
        return _render(request, message="Enter your email and preferences to subscribe.", error="Enter a valid email address.", values={"email": email, "skills": skill_text, "location": location, "work_mode": work_mode})
    if not skills:
        return _render(request, message="Choose at least one recognized technology skill.", error="Add one or more skills such as Python, React, or PostgreSQL.", values={"email": email, "skills": skill_text, "location": location, "work_mode": work_mode})
    if work_mode not in ALLOWED_WORK_MODES:
        return _render(request, message="Choose your job location preference.", error="Choose Any, Remote, or Local.", values={"email": email, "skills": skill_text, "location": location, "work_mode": "any"})

    subscription = db.query(AlertSubscription).filter_by(email=email).one_or_none()
    if subscription and subscription.status == "confirmed":
        return _render(request, message="If this address already receives alerts, use the preferences link in a recent alert. Otherwise, check your inbox to confirm your request.")

    confirm_token = secrets.token_urlsafe(32)
    unsubscribe_salt = secrets.token_hex(16)
    now = datetime.now(UTC)
    if not subscription:
        subscription = AlertSubscription(email=email, created_at=now)
        db.add(subscription)
    subscription.skills = ",".join(skills)
    subscription.location = location
    subscription.work_mode = work_mode
    subscription.status = "pending"
    subscription.confirm_token_hash = _token_hash(confirm_token)
    subscription.confirm_expires_at = now + CONFIRM_TTL
    subscription.unsubscribe_salt = unsubscribe_salt
    subscription.unsubscribe_token_hash = _token_hash(secrets.token_urlsafe(32))
    subscription.confirmed_at = None
    subscription.unsubscribed_at = None
    subscription.last_digest_sent_at = None
    db.flush()
    unsubscribe_token = _derive_unsubscribe_token(subscription)
    subscription.unsubscribe_token_hash = _token_hash(unsubscribe_token)
    db.commit()
    try:
        _send_confirmation(subscription, confirm_token, _derive_unsubscribe_token(subscription))
    except (httpx.HTTPError, RuntimeError):
        logger.exception("Could not send alert confirmation email")
        return _render(request, message="We could not send a confirmation email right now. Please try again shortly.", error="Email confirmation is temporarily unavailable.")
    return _render(request, message="Check your inbox for a confirmation link. Alerts start only after you confirm your email.")


@router.get("/alerts/confirm/{token}", response_class=HTMLResponse)
def confirm_subscription(token: str, request: Request, db: Session = Depends(get_db)):
    subscription = db.query(AlertSubscription).filter_by(confirm_token_hash=_token_hash(token)).one_or_none()
    now = datetime.now(UTC)
    if not subscription or subscription.status != "pending" or not subscription.confirm_expires_at or _as_utc(subscription.confirm_expires_at) <= now:
        return _render(request, message="That confirmation link is invalid or expired. Subscribe again to receive a new link.", error="Confirmation link unavailable.")
    subscription.status = "confirmed"
    subscription.confirmed_at = now
    subscription.confirm_token_hash = None
    subscription.confirm_expires_at = None
    db.commit()
    return RedirectResponse("/alerts?confirmed=1", status_code=303)


@router.get("/alerts/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe(token: str, request: Request, db: Session = Depends(get_db)):
    subscription = db.query(AlertSubscription).filter_by(unsubscribe_token_hash=_token_hash(token)).one_or_none()
    if subscription:
        # Remove the address and preferences immediately after opt-out.
        db.delete(subscription)
        db.commit()
    return _render(request, message="You have been unsubscribed from HireLoop job alerts.")


@router.post("/alerts/unsubscribe/{token}", response_class=HTMLResponse)
def one_click_unsubscribe(token: str, request: Request, db: Session = Depends(get_db)):
    # RFC 8058 one-click requests use POST and do not carry browser CSRF tokens.
    return unsubscribe(token, request, db)


def _get_manageable(token: str, db: Session) -> AlertSubscription | None:
    subscription = db.query(AlertSubscription).filter_by(unsubscribe_token_hash=_token_hash(token)).one_or_none()
    return subscription if subscription and subscription.status == "confirmed" else None


@router.get("/alerts/preferences/{token}", response_class=HTMLResponse)
def preferences_page(token: str, request: Request, db: Session = Depends(get_db)):
    subscription = _get_manageable(token, db)
    if not subscription:
        return _render(request, message="This preferences link is no longer active. Subscribe again to set up alerts.", error="Preferences unavailable.")
    return _render(request, message="Update your daily job alert preferences.", values={"email": subscription.email, "skills": subscription.skills, "location": subscription.location, "work_mode": subscription.work_mode}, preferences_token=token)


@router.post("/alerts/preferences/{token}", response_class=HTMLResponse)
async def update_preferences(token: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    verify_csrf(request, str(form.get("csrf_token", "")))
    subscription = _get_manageable(token, db)
    if not subscription:
        return _render(request, message="This preferences link is no longer active.", error="Preferences unavailable.")
    skill_text = str(form.get("skills", ""))[:2000]
    skills = extract_skills(skill_text)
    location = str(form.get("location", "")).strip()[:100]
    work_mode = str(form.get("work_mode", "any"))
    if not skills or work_mode not in ALLOWED_WORK_MODES:
        return _render(request, message="Update your daily job alert preferences.", error="Choose at least one recognized skill and a valid work location preference.", values={"email": subscription.email, "skills": skill_text, "location": location, "work_mode": work_mode}, preferences_token=token)
    subscription.skills = ",".join(skills)
    subscription.location = location
    subscription.work_mode = work_mode
    db.commit()
    return _render(request, message="Your alert preferences have been updated.", values={"email": subscription.email, "skills": subscription.skills, "location": subscription.location, "work_mode": subscription.work_mode}, preferences_token=token)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def matching_new_jobs(db: Session, subscription: AlertSubscription) -> list[Job]:
    cutoff = subscription.last_digest_sent_at or subscription.confirmed_at
    if not cutoff:
        return []
    query = db.query(Job).filter(
        Job.is_active.is_(True),
        or_(Job.source != "direct", Job.status == "approved"),
        or_(Job.expires_at.is_(None), Job.expires_at > datetime.now(UTC)),
        func.coalesce(Job.posted_at, Job.created_at) > cutoff,
    )
    if subscription.work_mode == "remote":
        query = query.filter(Job.is_remote.is_(True))
    elif subscription.work_mode == "local":
        query = query.filter(Job.is_remote.is_(False))
    if subscription.location:
        query = query.filter(Job.location.ilike(f"%{subscription.location}%"))
    selected_skills = {skill for skill in subscription.skills.split(",") if skill}
    return [
        job for job in query.order_by(func.coalesce(Job.posted_at, Job.created_at).asc(), Job.id.asc()).all()
        if selected_skills.intersection({skill.strip() for skill in (job.skills or "").split(",") if skill.strip()})
    ]


def digest_html(jobs: list[Job], *, manage_url: str, unsubscribe_url: str) -> str:
    rows = []
    for job in jobs:
        title = html.escape(job.title)
        company = html.escape(job.company)
        location = html.escape(job.location)
        href = f"{settings.site_url}/jobs/{job.id}"
        rows.append(f'<li><a href="{html.escape(href, quote=True)}">{title}</a> — {company} · {location}</li>')
    return (
        "<p>New HireLoop jobs matching your preferences:</p><ul>"
        + "".join(rows)
        + "</ul><p><a href=\""
        + html.escape(manage_url, quote=True)
        + "\">Manage preferences</a> · <a href=\""
        + html.escape(unsubscribe_url, quote=True)
        + "\">Unsubscribe</a></p>"
    )


def send_daily_digests(db: Session, email_sender=send_email) -> dict[str, int]:
    counts = {"sent": 0, "no_matches": 0, "failed": 0}
    subscribers = db.query(AlertSubscription).filter_by(status="confirmed").all()
    utc_day = datetime.now(UTC).date().isoformat()
    for subscription in subscribers:
        jobs = matching_new_jobs(db, subscription)
        if not jobs:
            counts["no_matches"] += 1
            continue
        unsubscribe_url = _unsubscribe_url_for(subscription)
        token = _derive_unsubscribe_token(subscription)
        unsubscribe_url = _unsubscribe_url(token)
        html_body = digest_html(jobs, manage_url=_manage_url(token), unsubscribe_url=unsubscribe_url)
        try:
            email_sender(
                subscription.email,
                f"Your HireLoop job alerts ({len(jobs)} new jobs)",
                html_body,
                idempotency_key=f"daily-digest-{subscription.id}-{utc_day}",
                unsubscribe_url=unsubscribe_url,
            )
        except (httpx.HTTPError, RuntimeError):
            logger.exception("Could not send daily alert to subscription id %s", subscription.id)
            counts["failed"] += 1
            continue
        subscription.last_digest_sent_at = datetime.now(UTC)
        db.commit()
        counts["sent"] += 1
    return counts


def _unsubscribe_url_for(subscription: AlertSubscription) -> str:
    return _unsubscribe_url(_derive_unsubscribe_token(subscription))


def _derive_unsubscribe_token(subscription: AlertSubscription) -> str:
    message = f"alerts:{subscription.id}:{subscription.unsubscribe_salt}".encode()
    return hmac.new(settings.secret_key.encode(), message, hashlib.sha256).hexdigest()
