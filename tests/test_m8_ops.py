"""Admin operations stats, health checks, and failure behavior."""

import re
from datetime import UTC, datetime

from jobboard.main import app, get_db
from jobboard.models import Application, Employer, Job, User


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, html
    return match.group(1)


def _register(register_account, email: str, company: str):
    return register_account(email=email, company=company)


def test_admin_stats_are_private_and_show_live_counts(client, db_session, monkeypatch, register_account):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.org")
    assert client.get("/admin/stats").status_code == 403
    assert _register(register_account, "employer@example.org", "Employer Co").status_code == 303
    assert client.get("/admin/stats").status_code == 403
    employer_user = db_session.query(User).filter_by(email="employer@example.org").one()
    employer = db_session.query(Employer).filter_by(user_id=employer_user.id).one()

    client.cookies.clear()
    assert _register(register_account, "admin@example.org", "Admin Co").status_code == 303
    now = datetime.now(UTC)
    direct_job = Job(
        source="direct",
        external_id="ops-direct",
        employer_id=employer.id,
        title="Engineer",
        company="Employer Co",
    )
    db_session.add_all(
        [
            direct_job,
            Job(source="remotive", external_id="ops-remotive", title="A", company="B", is_active=False),
            Job(source="remoteok", external_id="ops-remoteok", title="C", company="D"),
        ]
    )
    db_session.flush()
    db_session.add(
        Application(
            job_id=direct_job.id,
            employer_id=employer.id,
            candidate_email="candidate@example.net",
            candidate_note="A note",
            match_score=0,
            created_at=now,
            updated_at=now,
        )
    )
    db_session.commit()

    response = client.get("/admin/stats")
    assert response.status_code == 200
    assert "2 total" in response.text
    assert "2 added in the last 30 days" in response.text
    assert "1 received in the last 30 days" in response.text
    assert re.search(
        r"<th scope=\"row\" class=\"p-2\">remotive</th>\s*<td class=\"p-2\">1</td>\s*<td class=\"p-2\">0</td>",
        response.text,
    )
    assert "remoteok" in response.text and "direct" in response.text


def test_health_returns_503_when_database_check_fails(client):
    original = app.dependency_overrides[get_db]

    class BrokenDatabase:
        def execute(self, _statement):
            raise RuntimeError("database unavailable")

    def broken_db():
        yield BrokenDatabase()

    app.dependency_overrides[get_db] = broken_db
    try:
        response = client.get("/health")
    finally:
        app.dependency_overrides[get_db] = original

    assert response.status_code == 503
    assert response.json() == {"status": "unhealthy", "database": "unavailable"}
