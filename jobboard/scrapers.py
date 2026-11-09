"""Official JSON API clients for the two sources already approved by the spec."""

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

logger = logging.getLogger("hireloop.scrapers")
REMOTIVE_API = "https://remotive.com/api/remote-jobs"
REMOTEOK_API = "https://remoteok.com/api"
REMOTEOK_USER_AGENT = "HireLoopJobAggregator/1.0 (+https://github.com/shoaibnext/HireLoop; contact: hello@hireloop.pk)"
_last_request_at: dict[str, float] = {}


def _wait_for_interval(source: str, minimum_seconds: float) -> None:
    last = _last_request_at.get(source)
    if last is not None:
        remaining = minimum_seconds - (time.monotonic() - last)
        if remaining > 0:
            time.sleep(remaining)
    _last_request_at[source] = time.monotonic()


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def fetch_remotive(
    *, client: httpx.Client | None = None, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Fetch Remotive's delayed public feed and retain listings at least 24h old."""
    _wait_for_interval("remotive", 30.0)  # no more than two requests per minute
    owned_client = client is None
    request_client = client or httpx.Client(timeout=30.0)
    try:
        response = request_client.get(REMOTIVE_API, params={"limit": 100})
        response.raise_for_status()
        payload = response.json()
    finally:
        if owned_client:
            request_client.close()
    cutoff = (now or datetime.now(UTC)) - timedelta(hours=24)
    jobs = []
    for raw in payload.get("jobs", []):
        posted_at = _parse_datetime(raw.get("publication_date"))
        if posted_at is None or posted_at > cutoff:
            continue
        tags = raw.get("tags") or []
        jobs.append(
            {
                "external_id": str(raw.get("id", "")),
                "title": raw.get("title") or "",
                "company": raw.get("company_name") or "",
                "location": raw.get("candidate_required_location") or "Remote",
                "is_remote": True,
                "job_type": raw.get("job_type") or "full-time",
                "salary": raw.get("salary") or "",
                "tags": ", ".join(map(str, tags))
                if isinstance(tags, list)
                else str(tags),
                "description": raw.get("description") or "",
                "source_url": raw.get("url") or "",
                "posted_at": posted_at,
                "source_level": raw.get("seniority") or raw.get("seniority_level"),
            }
        )
    return jobs


def fetch_remoteok(*, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    """Fetch RemoteOK's public JSON feed with a descriptive User-Agent."""
    _wait_for_interval("remoteok", 1.0)
    owned_client = client is None
    request_client = client or httpx.Client(
        timeout=30.0, headers={"User-Agent": REMOTEOK_USER_AGENT}
    )
    try:
        response = request_client.get(
            REMOTEOK_API, headers={"User-Agent": REMOTEOK_USER_AGENT}
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if owned_client:
            request_client.close()
    if not isinstance(payload, list):
        raise TypeError("RemoteOK API response was not a JSON list")
    jobs = []
    for raw in payload:
        if not isinstance(raw, dict) or not raw.get("id") or not raw.get("position"):
            continue  # the feed's first object is metadata
        tags = raw.get("tags") or []
        jobs.append(
            {
                "external_id": str(raw["id"]),
                "title": raw.get("position") or "",
                "company": raw.get("company") or "",
                "location": raw.get("location") or "Remote",
                "is_remote": True,
                "job_type": raw.get("job_type") or "full-time",
                "salary": raw.get("salary") or "",
                "tags": ", ".join(map(str, tags))
                if isinstance(tags, list)
                else str(tags),
                "description": raw.get("description") or "",
                "source_url": raw.get("url") or "",
                "posted_at": _parse_datetime(raw.get("date")),
                "source_level": raw.get("seniority") or raw.get("level"),
            }
        )
    return jobs


SOURCE_FETCHERS = {"remotive": fetch_remotive, "remoteok": fetch_remoteok}
