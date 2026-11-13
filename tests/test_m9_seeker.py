"""M9 seeker experience, persistence, privacy, and no-email behavior."""

import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from jobboard import employer
from jobboard.models import (
    CVProfile,
    Job,
    RecentSearch,
    SeekerJobState,
    SeekerPreference,
    User,
)


def csrf(html):
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, html
    return match.group(1)


def signed_in(client, register_account, email):
    response = register_account(email=email)
    assert response.status_code == 303


def login(client, email):
    page = client.get("/login")
    return client.post(
        "/login",
        data={
            "csrf_token": csrf(page.text),
            "email": email,
            "password": "correct horse battery staple",
        },
        follow_redirects=False,
    )


def test_search_ranks_title_then_skills_then_company_then_description(
    client, db_session
):
    jobs = [
        Job(source="direct", external_id="rank-title", title="Python Engineer", company="A"),
        Job(source="direct", external_id="rank-skills", title="Backend Engineer", company="B", skills="python"),
        Job(source="direct", external_id="rank-company", title="Platform Engineer", company="Python House"),
        Job(source="direct", external_id="rank-description", title="Data Engineer", company="D", description="<p>Uses Python every day</p>"),
    ]
    db_session.add_all(jobs)
    db_session.commit()

    ranked = client.get("/?q=python&sort=relevance")
    assert ranked.status_code == 200
    positions = [ranked.text.index(job.title) for job in jobs]
    assert positions == sorted(positions)
    assert 'name="robots" content="noindex,follow"' in ranked.text


def test_posted_window_and_newest_sort_keep_existing_parameters(client, db_session):
    now = datetime.now(UTC)
    recent = Job(source="direct", external_id="posted-recent", title="Python Recent", company="A", posted_at=now - timedelta(hours=2), skills="python", level="senior")
    older = Job(source="direct", external_id="posted-older", title="Python Older", company="B", posted_at=now - timedelta(days=5), skills="python", level="senior")
    db_session.add_all([recent, older])
    db_session.commit()
    response = client.get("/?q=python&level=senior&posted=24h&sort=newest")
    assert response.status_code == 200
    assert "Python Recent" in response.text
    assert "Python Older" not in response.text
    assert 'name="robots" content="noindex,follow"' in response.text
    assert 'option value="24h" selected' in response.text
    assert 'option value="newest" selected' in response.text


def test_saved_job_toggle_detail_and_logged_out_return(
    client, db_session, register_account, seed_jobs
):
    job = seed_jobs[0]
    signed_in(client, register_account, "seeker@example.test")
    client.cookies.clear()
    response = client.post(
        f"/jobs/{job.id}/save",
        data={"csrf_token": csrf(client.get(f"/jobs/{job.id}").text)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert login(client, "seeker@example.test").headers["location"] == f"/jobs/{job.id}"
    detail = client.get(f"/jobs/{job.id}")
    assert db_session.query(SeekerJobState).count() == 0
    response = client.post(
        f"/jobs/{job.id}/save",
        data={"csrf_token": csrf(detail.text)},
        follow_redirects=False,
    )
    assert response.headers["location"] == f"/jobs/{job.id}"
    saved = db_session.query(SeekerJobState).one()
    db_session.refresh(saved)
    assert saved.is_saved is True
    assert "Saved" in client.get("/my-jobs?tab=saved").text
    detail = client.get(f"/jobs/{job.id}")
    assert 'aria-label="Remove saved job"' in detail.text


def test_saved_and_private_job_data_are_isolated_between_accounts(
    client, db_session, register_account, seed_jobs
):
    job = seed_jobs[0]
    signed_in(client, register_account, "first-seeker@example.test")
    page = client.get("/")
    client.post(f"/jobs/{job.id}/save", data={"csrf_token": csrf(page.text)})
    client.post(
        f"/jobs/{job.id}/state",
        data={"csrf_token": csrf(client.get("/my-jobs?tab=saved").text), "status": "interviewing", "note": "First person's note"},
    )
    first = db_session.query(User).filter_by(email="first-seeker@example.test").one()
    first_state = db_session.query(SeekerJobState).filter_by(user_id=first.id, job_id=job.id).one()

    client.cookies.clear()
    signed_in(client, register_account, "second-seeker@example.test")
    second_page = client.get("/my-jobs?tab=saved")
    assert "Senior Python Engineer" not in second_page.text
    assert "First person's note" not in second_page.text
    client.post(
        f"/jobs/{job.id}/state",
        data={"csrf_token": csrf(client.get("/").text), "status": "offer", "note": "Second person's note"},
    )
    db_session.refresh(first_state)
    assert first_state.application_status == "interviewing"
    assert first_state.private_note == "First person's note"


def test_my_jobs_tracks_aggregated_application_status_notes_and_hide_undo(
    client, db_session, register_account, seed_jobs
):
    job = seed_jobs[1]
    signed_in(client, register_account, "tracker@example.test")
    home = client.get("/")
    response = client.post(
        f"/jobs/{job.id}/state",
        data={"csrf_token": csrf(home.text), "quick_apply": "1"},
    )
    assert response.status_code == 200
    applied = client.get("/my-jobs?tab=applied")
    assert "Junior React Developer" in applied.text
    response = client.post(
        f"/jobs/{job.id}/state",
        data={"csrf_token": csrf(applied.text), "status": "offer", "note": "Follow up Friday"},
    )
    assert response.status_code == 200
    offer = client.get("/my-jobs?tab=offer")
    assert "Junior React Developer" in offer.text
    assert "Follow up Friday" in offer.text
    response = client.post(
        f"/jobs/{job.id}/hide",
        data={"csrf_token": csrf(client.get("/").text)},
    )
    assert response.status_code == 200
    assert "Junior React Developer" not in client.get("/").text
    hidden = client.get("/my-jobs?tab=offer")
    assert "Junior React Developer" in hidden.text
    assert "Hidden jobs" in hidden.text
    response = client.post(
        f"/jobs/{job.id}/state",
        data={"csrf_token": csrf(hidden.text), "action": "restore"},
    )
    assert response.status_code == 200
    assert "Junior React Developer" in client.get("/").text


def test_preferences_persist_and_drive_recommendations_without_fabrication(
    client, db_session, register_account, seed_jobs
):
    signed_in(client, register_account, "recommend@example.test")
    dashboard = client.get("/account")
    assert "Add profile details to get recommendations" in dashboard.text
    db_session.add(
        CVProfile(
            user_id=db_session.query(User).filter_by(email="recommend@example.test").one().id,
            session_key_hash="a" * 64,
            extracted_text="Python FastAPI",
            skills="python,fastapi",
            created_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
    )
    db_session.commit()
    dashboard = client.get("/account")
    assert "Senior Python Engineer" in dashboard.text
    assert "Matched skills: fastapi, python" in dashboard.text
    form = client.get("/preferences")
    saved = client.post(
        "/preferences",
        data={
            "csrf_token": csrf(form.text),
            "target_roles": "Senior Python Engineer\nBackend Engineer",
            "skills": "Python, AWS, not-a-real-skill",
            "location": "Lahore",
            "minimum_salary": "150000",
            "salary_currency": "PKR",
            "remote_only": "1",
        },
        follow_redirects=False,
    )
    assert saved.status_code == 303
    pref = db_session.query(SeekerPreference).one()
    assert pref.target_roles.startswith("Senior Python Engineer")
    assert pref.skills == "python,aws"
    assert pref.location == "Lahore" and pref.remote_only
    assert pref.salary_currency == "PKR"
    assert str(pref.minimum_salary) == "150000.00"


def test_recent_searches_keep_five_and_can_be_cleared_per_account(
    client, db_session, register_account
):
    signed_in(client, register_account, "search-history@example.test")
    for index in range(7):
        assert client.get(f"/?q=history-{index}").status_code == 200
    user = db_session.query(User).filter_by(email="search-history@example.test").one()
    assert db_session.query(RecentSearch).filter_by(user_id=user.id).count() == 5
    page = client.get("/")
    assert "Recent searches:" in page.text
    response = client.post(
        "/recent-searches/clear", data={"csrf_token": csrf(page.text)}, follow_redirects=False
    )
    assert response.status_code == 303
    assert db_session.query(RecentSearch).filter_by(user_id=user.id).count() == 0


def test_seeker_mutations_require_csrf_and_email_delivery_is_not_used(
    client, register_account, monkeypatch, seed_jobs
):
    signed_in(client, register_account, "no-email@example.test")
    monkeypatch.setattr(
        employer, "settings", replace(employer.settings, email_delivery_verified=False)
    )

    def email_must_not_send(*_args, **_kwargs):
        raise AssertionError("M9 should not send email")

    monkeypatch.setattr(employer, "_send_email_otp", email_must_not_send)
    job = seed_jobs[0]
    assert client.post(f"/jobs/{job.id}/save").status_code == 403
    assert client.post("/preferences", data={"skills": "Python"}).status_code == 403
    assert client.post("/recent-searches/clear").status_code == 403
    preferences = client.get("/preferences")
    assert client.post(
        "/preferences",
        data={"csrf_token": csrf(preferences.text), "skills": "Python"},
        follow_redirects=False,
    ).status_code == 303
