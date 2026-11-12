"""HTTP clients for the approved job listing APIs."""

import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("hireloop.scrapers")
REMOTIVE_API = "https://remotive.com/api/remote-jobs"
REMOTEOK_API = "https://remoteok.com/api"
SKILLZDUNYA_API = "https://skillzdunya.com/api/v1/jobs"
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


def _display_value(value: Any) -> str:
    """Flatten API label objects and lists into readable text."""
    if isinstance(value, dict):
        for key in ("title", "name", "label", "value"):
            if value.get(key):
                return str(value[key])
        return ""
    if isinstance(value, list):
        return ", ".join(
            rendered for item in value if (rendered := _display_value(item))
        )
    return str(value or "")


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


def fetch_skillzdunya(*, client: httpx.Client | None = None) -> list[dict[str, Any]]:
    """Fetch Pakistan listings from SkillzDunya's free public JSON API."""
    owned_client = client is None
    request_client = client or httpx.Client(timeout=30.0)
    try:
        response = request_client.get(SKILLZDUNYA_API, params={"per_page": 100})
        response.raise_for_status()
        payload = response.json()
    finally:
        if owned_client:
            request_client.close()

    # The API documents a paginated list; accept its common envelope variants so
    # harmless response-wrapper changes do not break the other source runs.
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = payload.get("data", payload.get("jobs", []))
        if isinstance(records, dict):
            records = records.get("data", records.get("items", []))
    else:
        raise TypeError("SkillzDunya API response was not a JSON list or object")
    if not isinstance(records, list):
        raise TypeError("SkillzDunya API jobs field was not a JSON list")

    jobs = []
    for raw in records:
        if not isinstance(raw, dict):
            continue
        external_id = raw.get("id") or raw.get("slug")
        title = raw.get("title")
        if not external_id or not title:
            continue
        cities = (
            raw.get("cities")
            or raw.get("city")
            or raw.get("location")
            or "Pakistan"
        )
        location = _display_value(cities) or "Pakistan"
        categories = raw.get("categories") or raw.get("category") or []
        qualifications = raw.get("qualifications") or []
        skills = raw.get("skills") or []
        tags = ", ".join(
            value
            for value in (
                _display_value(categories),
                _display_value(qualifications),
                _display_value(skills),
            )
            if value
        )
        description = (
            raw.get("description")
            or raw.get("snippet")
            or raw.get("summary")
            or raw.get("body")
            or ""
        )
        source_url = str(raw.get("url") or raw.get("canonical_url") or "")
        parsed_url = urlparse(source_url)
        if parsed_url.scheme != "https" or parsed_url.hostname != "skillzdunya.com":
            continue
        jobs.append(
            {
                "external_id": str(external_id),
                "title": str(title),
                "company": _display_value(
                    raw.get("organization") or raw.get("company")
                ) or "Employer not listed",
                "location": location[:255],
                "is_remote": bool(raw.get("international", False)),
                "job_type": _display_value(
                    raw.get("employment_type") or raw.get("job_type") or raw.get("type")
                ) or "full-time",
                "salary": _display_value(raw.get("salary") or raw.get("salary_range")),
                "tags": tags,
                "description": str(description),
                "source_url": source_url,
                "posted_at": _parse_datetime(
                    raw.get("published_at")
                    or raw.get("posted_at")
                    or raw.get("date")
                ),
                "source_level": _display_value(
                    raw.get("experience") or raw.get("experience_level")
                ),
            }
        )
    return jobs


SOURCE_FETCHERS = {
    "remotive": fetch_remotive,
    "remoteok": fetch_remoteok,
    "skillzdunya": fetch_skillzdunya,
}
