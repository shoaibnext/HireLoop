from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.orm import sessionmaker

from jobboard.ingest import deduplicate_sources, run_ingest
from jobboard.models import IngestRun, Job
from jobboard.scrapers import REMOTEOK_USER_AGENT, fetch_remoteok, fetch_remotive


def _job(**overrides):
    return {
        "external_id": "remote-1",
        "title": "Senior Python Developer",
        "company": "Example Co",
        "location": "Remote",
        "is_remote": True,
        "job_type": "full-time",
        "salary": "",
        "tags": "python, fastapi",
        "description": "<p>Build APIs with Python</p><script>drop()</script>",
        "source_url": "https://remoteok.com/jobs/remote-1",
        "posted_at": datetime(2026, 10, 1, tzinfo=UTC),
        "source_level": None,
        **overrides,
    }


def test_ingest_twice_upserts_and_refreshes_last_seen(db_session):
    first_time = datetime(2026, 11, 1, 3, tzinfo=UTC)
    second_time = first_time + timedelta(minutes=2)
    payload = _job()
    first = run_ingest(
        session_factory=_session_factory(db_session),
        fetchers={"remoteok": lambda: [payload]},
        now_provider=lambda: first_time,
    )
    row = (
        db_session.query(Job).filter_by(source="remoteok", external_id="remote-1").one()
    )
    assert first["remoteok"]["new"] == 1
    assert row.last_seen_at.replace(tzinfo=UTC) == first_time
    payload["description"] = "<p>Updated Python role</p>"
    second = run_ingest(
        session_factory=_session_factory(db_session),
        fetchers={"remoteok": lambda: [payload]},
        now_provider=lambda: second_time,
    )
    db_session.refresh(row)
    assert second["remoteok"]["updated"] == 1
    assert (
        db_session.query(Job)
        .filter_by(source="remoteok", external_id="remote-1")
        .count()
        == 1
    )
    assert row.last_seen_at.replace(tzinfo=UTC) == second_time
    assert "<script>" not in row.description
    assert row.skills == "python,fastapi"
    assert row.level == "senior"


def _session_factory(session):
    return sessionmaker(
        bind=session.get_bind(), autoflush=False, expire_on_commit=False
    )


def test_old_jobs_expire_during_ingest_and_inactive_listing_is_hidden(
    client, db_session, seed_jobs
):
    now = datetime(2026, 11, 9, tzinfo=UTC)
    stale = seed_jobs[0]
    stale.last_seen_at = now - timedelta(days=46)
    db_session.commit()
    run_ingest(
        fetchers={},
        session_factory=_session_factory(db_session),
        now_provider=lambda: now,
    )
    db_session.refresh(stale)
    assert stale.is_active is False
    assert "Senior Python Engineer" not in client.get("/").text
    assert all(
        item["title"] != stale.title for item in client.get("/api/jobs").json()["items"]
    )
    response = client.get(f"/jobs/{stale.id}-senior-python-engineer")
    assert response.status_code == 410
    assert "no longer available" in response.text


def test_inactive_is_hidden_from_search_and_counts(client, db_session, seed_jobs):
    seed_jobs[1].is_active = False
    db_session.commit()
    response = client.get("/?q=react")
    assert "Junior React Developer" not in response.text
    assert "0 jobs" in response.text
    assert client.get("/api/jobs?q=react").json()["total"] == 0


def test_level_and_skill_search_use_normalized_columns(client, db_session):
    db_session.add_all(
        [
            Job(
                source="direct",
                external_id="analyst",
                title="Platform Analyst",
                company="A",
                level="senior",
                skills="python,sql",
            ),
            Job(
                source="direct",
                external_id="mid",
                title="Platform Analyst II",
                company="B",
                level="mid",
                skills="python",
            ),
        ]
    )
    db_session.commit()
    senior = client.get("/?level=senior")
    assert senior.status_code == 200
    assert "1 jobs" in senior.text
    assert "Platform Analyst</a>" in senior.text
    assert "Platform Analyst II" not in senior.text
    python = client.get("/?q=python")
    assert "2 jobs" in python.text


def test_cross_source_dedupe_keeps_earliest_posted_without_deleting(db_session):
    earlier = Job(
        source="remotive",
        external_id="r1",
        title="Python Engineer",
        company="Acme  Inc",
        posted_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    later = Job(
        source="remoteok",
        external_id="r2",
        title=" python engineer ",
        company="acme inc",
        posted_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    db_session.add_all([earlier, later])
    db_session.commit()
    assert deduplicate_sources(db_session) == 1
    assert earlier.is_active is True
    assert later.is_active is False
    assert db_session.query(Job).count() == 2


def test_source_failure_is_recorded_and_next_source_runs(db_session):
    now = datetime(2026, 11, 9, 3, tzinfo=UTC)

    def fail():
        raise RuntimeError("upstream unavailable")

    results = run_ingest(
        session_factory=_session_factory(db_session),
        fetchers={"remotive": fail, "remoteok": lambda: [_job()]},
        now_provider=lambda: now,
    )
    assert results["remotive"]["errors"]
    assert results["remoteok"]["new"] == 1
    runs = {run.source: run for run in db_session.query(IngestRun).all()}
    assert runs["remotive"].status == "failed"
    assert runs["remoteok"].status == "ok"


def test_remotive_is_skipped_within_six_hours(db_session):
    now = datetime(2026, 11, 9, 12, tzinfo=UTC)
    db_session.add(
        IngestRun(source="remotive", started_at=now - timedelta(hours=5), status="ok")
    )
    db_session.commit()
    called = []
    results = run_ingest(
        session_factory=_session_factory(db_session),
        fetchers={"remotive": lambda: called.append(True) or []},
        now_provider=lambda: now,
    )
    assert called == []
    assert results["remotive"]["errors"][0].startswith("Skipped:")
    assert db_session.query(IngestRun).count() == 2


def test_remotive_is_capped_at_four_fetches_per_utc_day(db_session):
    now = datetime(2026, 11, 9, 23, tzinfo=UTC)
    for hour in (0, 6, 12, 18):
        db_session.add(
            IngestRun(source="remotive", started_at=now.replace(hour=hour), status="ok")
        )
    db_session.commit()
    results = run_ingest(
        session_factory=_session_factory(db_session),
        fetchers={"remotive": list},
        now_provider=lambda: now,
    )
    assert results["remotive"]["errors"][0].startswith("Skipped:")


def test_dry_run_does_not_write(db_session):
    now = datetime(2026, 11, 9, 3, tzinfo=UTC)
    results = run_ingest(
        dry_run=True,
        session_factory=_session_factory(db_session),
        fetchers={"remoteok": lambda: [_job()]},
        now_provider=lambda: now,
    )
    assert results["remoteok"]["new"] == 1
    assert db_session.query(Job).count() == 0
    assert db_session.query(IngestRun).count() == 0


def test_scrapers_use_mocked_official_json_feeds(monkeypatch):
    monkeypatch.setattr("jobboard.scrapers._wait_for_interval", lambda *_args: None)
    sent_requests = []

    def handler(request):
        sent_requests.append(request)
        if request.url.host == "remotive.com":
            return httpx.Response(
                200,
                json={
                    "jobs": [
                        {
                            "id": 9,
                            "title": "Python Dev",
                            "company_name": "X",
                            "publication_date": "2026-11-07T00:00:00Z",
                            "url": "https://remotive.com/jobs/9",
                            "description": "<p>Python</p>",
                        },
                        {
                            "id": 10,
                            "title": "Recent role",
                            "company_name": "Y",
                            "publication_date": "2026-11-09T00:00:00Z",
                            "url": "https://remotive.com/jobs/10",
                        },
                    ]
                },
            )
        return httpx.Response(
            200,
            json=[
                {"legal": "metadata"},
                {
                    "id": 15,
                    "position": "React Dev",
                    "company": "Y",
                    "url": "https://remoteok.com/jobs/15",
                    "date": 1794182400,
                    "tags": ["react"],
                },
            ],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    now = datetime(2026, 11, 9, 1, tzinfo=UTC)
    remotive = fetch_remotive(client=client, now=now)
    remoteok = fetch_remoteok(client=client)
    client.close()
    assert [job["external_id"] for job in remotive] == ["9"]
    assert len(remoteok) == 1
    assert sent_requests[0].url.host == "remotive.com"
    assert sent_requests[1].headers["user-agent"] == REMOTEOK_USER_AGENT
