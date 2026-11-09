"""Per-source ingestion, freshness expiry, and cross-feed deduplication."""

import logging
import re
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import nh3
from sqlalchemy.orm import Session, sessionmaker

from .models import IngestRun, Job, SessionLocal
from .scrapers import SOURCE_FETCHERS
from .skills import detect_level, extract_skills

logger = logging.getLogger("hireloop.ingest")
JOB_HTML_TAGS = {
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
MAX_ERRORS_LENGTH = 10_000


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _normalized_identity(company: str, title: str) -> tuple[str, str]:
    normalize = lambda value: re.sub(
        r"\s+", " ", re.sub(r"[^\w]+", " ", value.casefold())
    ).strip()
    return normalize(company), normalize(title)


def _source_attempts_today(db: Session, now: datetime) -> int:
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    runs = (
        db.query(IngestRun)
        .filter(IngestRun.source == "remotive", IngestRun.started_at >= today)
        .all()
    )
    return sum(not (run.errors or "").startswith("Skipped:") for run in runs)


def remotive_skip_reason(db: Session, now: datetime | None = None) -> str | None:
    now = _utc(now or datetime.now(UTC))
    last_run = (
        db.query(IngestRun)
        .filter(IngestRun.source == "remotive")
        .order_by(IngestRun.started_at.desc(), IngestRun.id.desc())
        .first()
    )
    if last_run and _utc(last_run.started_at) > now - timedelta(hours=6):
        return "last Remotive run was less than 6 hours ago"
    if _source_attempts_today(db, now) >= 4:
        return "Remotive has already been fetched 4 times since 00:00 UTC"
    return None


def _count_new_and_updated(
    db: Session, source: str, jobs: list[dict[str, Any]]
) -> tuple[int, int]:
    new = updated = 0
    for raw in jobs:
        external_id = str(raw.get("external_id") or "").strip()
        if not external_id:
            continue
        if db.query(Job.id).filter_by(source=source, external_id=external_id).first():
            updated += 1
        else:
            new += 1
    return new, updated


def _upsert_jobs(
    db: Session, source: str, payload: list[dict[str, Any]], now: datetime
) -> tuple[int, int, list[str]]:
    new_count = updated_count = 0
    errors: list[str] = []
    for index, raw in enumerate(payload):
        try:
            external_id = str(raw.get("external_id") or "").strip()
            title = str(raw.get("title") or "").strip()
            if not external_id or not title:
                raise ValueError("job is missing external_id or title")
            values = {
                "title": title[:255],
                "company": str(raw.get("company") or "")[:255],
                "location": str(raw.get("location") or "")[:255],
                "is_remote": bool(raw.get("is_remote", False)),
                "job_type": str(raw.get("job_type") or "full-time")[:40],
                "salary": str(raw.get("salary") or "")[:255],
                "tags": str(raw.get("tags") or "")[:1000],
                "description": nh3.clean(
                    str(raw.get("description") or ""),
                    tags=JOB_HTML_TAGS,
                    attributes={},
                    link_rel=None,
                ),
                "source_url": str(raw.get("source_url") or "")[:2048],
                "posted_at": _utc(raw.get("posted_at")),
                "last_seen_at": now,
                "is_active": True,
                "level": detect_level(
                    title,
                    str(raw["source_level"]) if raw.get("source_level") else None,
                ),
                "skills": ",".join(
                    extract_skills(
                        title,
                        str(raw.get("tags") or ""),
                        str(raw.get("description") or ""),
                    )
                ),
            }
            row = (
                db.query(Job)
                .filter_by(source=source, external_id=external_id)
                .one_or_none()
            )
            if row is None:
                db.add(Job(source=source, external_id=external_id, **values))
                new_count += 1
            else:
                for key, value in values.items():
                    setattr(row, key, value)
                updated_count += 1
        except Exception as exc:
            errors.append(f"job[{index}]: {type(exc).__name__}: {exc}")
            logger.exception("Skipping invalid %s job at index %d", source, index)
    return new_count, updated_count, errors


def expire_stale_jobs(db: Session, now: datetime | None = None) -> int:
    cutoff = _utc(now or datetime.now(UTC)) - timedelta(days=45)
    changed = (
        db.query(Job)
        .filter(Job.is_active.is_(True), Job.last_seen_at < cutoff)
        .update({Job.is_active: False}, synchronize_session=False)
    )
    return changed


def deduplicate_sources(db: Session) -> int:
    rows = (
        db.query(Job)
        .filter(Job.source.in_(tuple(SOURCE_FETCHERS)), Job.is_active.is_(True))
        .order_by(Job.id)
        .all()
    )
    groups: dict[tuple[str, str], list[Job]] = defaultdict(list)
    for job in rows:
        identity = _normalized_identity(job.company, job.title)
        if all(identity):
            groups[identity].append(job)
    deactivated = 0
    for group in groups.values():
        if len({job.source for job in group}) < 2:
            continue
        winner = min(
            group,
            key=lambda job: (_utc(job.posted_at) or _utc(job.created_at), job.id),
        )
        for job in group:
            if job.id != winner.id:
                job.is_active = False
                deactivated += 1
    return deactivated


def run_ingest(
    *,
    dry_run: bool = False,
    session_factory: sessionmaker = SessionLocal,
    fetchers: dict[str, Callable[[], list[dict[str, Any]]]] | None = None,
    now_provider: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, dict[str, Any]]:
    """Run each source independently; one source failure never blocks another."""
    fetchers = SOURCE_FETCHERS if fetchers is None else fetchers
    results: dict[str, dict[str, Any]] = {}
    for source, fetcher in fetchers.items():
        started = _utc(now_provider())
        errors: list[str] = []
        session = session_factory()
        run_id = None
        try:
            skip_reason = (
                remotive_skip_reason(session, started) if source == "remotive" else None
            )
            if skip_reason:
                logger.info("Skipping Remotive fetch: %s", skip_reason)
                if not dry_run:
                    run = IngestRun(
                        source=source,
                        started_at=started,
                        fetched=0,
                        new=0,
                        updated=0,
                        errors=f"Skipped: {skip_reason}",
                        status="ok",
                        finished_at=_utc(now_provider()),
                    )
                    session.add(run)
                    session.commit()
                results[source] = {
                    "fetched": 0,
                    "new": 0,
                    "updated": 0,
                    "errors": [f"Skipped: {skip_reason}"],
                }
                continue

            run = None
            last_source_run = None
            if source == "remoteok":
                last_source_run = (
                    session.query(IngestRun)
                    .filter(IngestRun.source == source)
                    .order_by(IngestRun.started_at.desc(), IngestRun.id.desc())
                    .first()
                )
            if not dry_run:
                run = IngestRun(source=source, started_at=started, status="ok")
                session.add(run)
                session.commit()
                run_id = run.id

            # Persisted timestamps enforce spacing across separate CLI processes.
            if source == "remoteok" and last_source_run:
                elapsed = (started - _utc(last_source_run.started_at)).total_seconds()
                if elapsed < 1:
                    time.sleep(1 - max(0, elapsed))

            try:
                jobs = fetcher()
                if not isinstance(jobs, list):
                    raise TypeError("source fetcher must return a list")
                if dry_run:
                    new_count, updated_count = _count_new_and_updated(
                        session, source, jobs
                    )
                else:
                    new_count, updated_count, errors = _upsert_jobs(
                        session, source, jobs, started
                    )
                    session.flush()
                    run = session.get(IngestRun, run_id)
                    run.fetched = len(jobs)
                    run.new = new_count
                    run.updated = updated_count
                    run.errors = "\n".join(errors)[:MAX_ERRORS_LENGTH]
                    run.status = "failed" if errors else "ok"
                    run.finished_at = _utc(now_provider())
                    session.commit()
                results[source] = {
                    "fetched": len(jobs),
                    "new": new_count,
                    "updated": updated_count,
                    "errors": errors,
                }
                logger.info(
                    "Ingest %s: fetched=%d new=%d updated=%d errors=%d",
                    source,
                    len(jobs),
                    new_count,
                    updated_count,
                    len(errors),
                )
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                errors.append(message)
                logger.exception("Ingestion failed for source %s", source)
                if not dry_run and run_id is not None:
                    session.rollback()
                    run = session.get(IngestRun, run_id)
                    run.fetched = 0
                    run.new = 0
                    run.updated = 0
                    run.errors = message[:MAX_ERRORS_LENGTH]
                    run.status = "failed"
                    run.finished_at = _utc(now_provider())
                    session.commit()
                results[source] = {
                    "fetched": 0,
                    "new": 0,
                    "updated": 0,
                    "errors": errors,
                }
        finally:
            session.close()

    if not dry_run:
        with session_factory() as session:
            expired = expire_stale_jobs(session, _utc(now_provider()))
            deduped = deduplicate_sources(session)
            session.commit()
            logger.info("Maintenance: expired=%d deduplicated=%d", expired, deduped)
    return results


def print_stats(session_factory: sessionmaker = SessionLocal) -> None:
    with session_factory() as session:
        runs = (
            session.query(IngestRun)
            .order_by(IngestRun.started_at.desc(), IngestRun.id.desc())
            .limit(10)
            .all()
        )
        if not runs:
            print("No ingest runs recorded.")
            return
        print("id | source | started_at | finished_at | status | fetched | new | updated | errors")
        for run in runs:
            print(
                f"{run.id} | {run.source} | {run.started_at} | {run.finished_at or ''} | {run.status} | {run.fetched} | {run.new} | {run.updated} | {(run.errors or '').replace(chr(10), '; ')}"
            )
