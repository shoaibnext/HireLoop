import json
import re

from jobboard.models import Employer, Job, User


def _csrf(response):
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match, response.text
    return match.group(1)


def _register(client, *, email, company):
    page = client.get("/signup")
    return client.post(
        "/signup",
        data={
            "csrf_token": _csrf(page),
            "email": email,
            "password": "correct horse battery staple",
            "company_name": company,
            "website": "https://example.org",
        },
        follow_redirects=False,
    )


def _post_job(client, *, title="Senior Python Engineer"):
    page = client.get("/post-job")
    return client.post(
        "/post-job",
        data={
            "csrf_token": _csrf(page),
            "title": title,
            "location": "Lahore, Pakistan",
            "job_type": "full-time",
            "salary_min": "150000",
            "salary_max": "250000",
            "salary_currency": "PKR",
            "description": "<p>Build Python services.</p><script>bad()</script>",
            "apply_url": "https://example.org/apply",
            "remote": "1",
            "featured_requested": "1",
        },
        follow_redirects=False,
    )


def test_employer_can_post_then_admin_approve_and_feature(
    client, db_session, monkeypatch
):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.test")
    response = _register(
        client, email="employer@example.test", company="Example Systems"
    )
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    employer_user = (
        db_session.query(User).filter_by(email="employer@example.test").one()
    )
    assert employer_user.password_hash.startswith("$2")
    employer = db_session.query(Employer).filter_by(user_id=employer_user.id).one()

    assert client.get("/employer/dashboard").status_code == 200
    assert client.get("/post-job").status_code == 200
    assert _post_job(client).status_code == 303
    job = db_session.query(Job).filter_by(employer_id=employer.id).one()
    assert job.status == "pending"
    assert job.expires_at is not None
    assert job.salary_currency == "PKR"
    assert job.salary_min == 150000
    assert "<script>" not in job.description
    assert "payment instructions" in client.get("/employer/dashboard").text.lower()
    assert client.get(f"/jobs/{job.id}-senior-python-engineer").status_code == 404
    assert f"/jobs/{job.id}-" not in client.get("/sitemap.xml").text

    dashboard = client.get("/employer/dashboard")
    assert (
        client.post(
            "/logout", data={"csrf_token": _csrf(dashboard)}, follow_redirects=False
        ).status_code
        == 303
    )
    assert (
        _register(
            client, email="admin@example.test", company="Admin account"
        ).status_code
        == 303
    )
    assert client.get("/admin/jobs").status_code == 200
    admin_page = client.get("/admin/jobs")
    csrf = _csrf(admin_page)
    approved = client.post(
        f"/admin/jobs/{job.id}/moderate",
        data={"csrf_token": csrf, "action": "approve"},
        follow_redirects=False,
    )
    assert approved.status_code == 303
    db_session.refresh(job)
    assert job.status == "approved" and job.is_active
    assert f"/jobs/{job.id}-" in client.get("/sitemap.xml").text
    detail = client.get(f"/jobs/{job.id}-senior-python-engineer")
    assert detail.status_code == 200
    assert "application/ld+json" in detail.text
    structured = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', detail.text, re.DOTALL
    )
    data = json.loads(structured.group(1))
    assert data["@type"] == "JobPosting"
    assert data["baseSalary"]["currency"] == "PKR"
    assert data["jobLocationType"] == "TELECOMMUTE"

    client.post(
        f"/admin/jobs/{job.id}/moderate",
        data={
            "csrf_token": csrf,
            "action": "mark_paid",
            "payment_note": "Bank transfer reference 123",
        },
    )
    db_session.refresh(job)
    assert job.is_paid is True and job.payment_note == "Bank transfer reference 123"
    client.post(
        f"/admin/jobs/{job.id}/moderate",
        data={"csrf_token": csrf, "action": "feature"},
    )
    db_session.refresh(job)
    assert job.featured_until is not None
    assert "Featured" in client.get("/").text


def test_non_owner_cannot_edit_job(client, db_session):
    assert (
        _register(client, email="first@example.test", company="First Co").status_code
        == 303
    )
    assert _post_job(client, title="Owner-only position").status_code == 303
    job = db_session.query(Job).filter_by(title="Owner-only position").one()
    dashboard = client.get("/employer/dashboard")
    client.post("/logout", data={"csrf_token": _csrf(dashboard)})
    assert (
        _register(client, email="second@example.test", company="Second Co").status_code
        == 303
    )
    assert client.get(f"/employer/jobs/{job.id}/edit").status_code == 403
    assert client.get("/admin/jobs").status_code == 403
    response = client.post(
        f"/employer/jobs/{job.id}/edit",
        data={
            "csrf_token": _csrf(client.get("/employer/dashboard")),
            "title": "stolen",
        },
    )
    assert response.status_code == 403
    db_session.refresh(job)
    assert job.title == "Owner-only position"


def test_employer_can_sign_in_and_logout(client):
    assert (
        _register(client, email="signin@example.test", company="Sign In Co").status_code
        == 303
    )
    dashboard = client.get("/employer/dashboard")
    assert (
        client.post(
            "/logout", data={"csrf_token": _csrf(dashboard)}, follow_redirects=False
        ).status_code
        == 303
    )
    page = client.get("/login")
    token = _csrf(page)
    bad_login = client.post(
        "/login",
        data={
            "csrf_token": token,
            "email": "signin@example.test",
            "password": "wrong password",
        },
    )
    assert bad_login.status_code == 200
    assert "incorrect" in bad_login.text
    good_login = client.post(
        "/login",
        data={
            "csrf_token": token,
            "email": "signin@example.test",
            "password": "correct horse battery staple",
        },
        follow_redirects=False,
    )
    assert good_login.status_code == 303
    assert client.get("/employer/dashboard").status_code == 200
    fresh_token = _csrf(client.get("/employer/dashboard"))
    assert (
        client.post(
            "/logout", data={"csrf_token": fresh_token}, follow_redirects=False
        ).status_code
        == 303
    )
    assert client.get("/employer/dashboard", follow_redirects=False).status_code == 303


def test_owner_can_edit_close_and_renew_a_job(client, db_session):
    assert (
        _register(
            client, email="owner-actions@example.test", company="Owner Actions"
        ).status_code
        == 303
    )
    assert _post_job(client, title="Initial title").status_code == 303
    job = db_session.query(Job).filter_by(title="Initial title").one()
    edit_page = client.get(f"/employer/jobs/{job.id}/edit")
    assert edit_page.status_code == 200
    edit_data = {
        "csrf_token": _csrf(edit_page),
        "title": "Updated title",
        "location": "Karachi, Pakistan",
        "job_type": "contract",
        "description": "Updated description",
        "apply_email": "jobs@example.test",
    }
    assert (
        client.post(
            f"/employer/jobs/{job.id}/edit", data=edit_data, follow_redirects=False
        ).status_code
        == 303
    )
    db_session.refresh(job)
    assert job.title == "Updated title"
    dashboard = client.get("/employer/dashboard")
    assert (
        client.post(
            f"/employer/jobs/{job.id}/close",
            data={"csrf_token": _csrf(dashboard)},
            follow_redirects=False,
        ).status_code
        == 303
    )
    db_session.refresh(job)
    assert job.status == "closed" and job.is_active is False
    dashboard = client.get("/employer/dashboard")
    assert (
        client.post(
            f"/employer/jobs/{job.id}/renew",
            data={"csrf_token": _csrf(dashboard)},
            follow_redirects=False,
        ).status_code
        == 303
    )
    db_session.refresh(job)
    assert job.status == "pending" and job.is_active is True
    assert job.expires_at is not None


def test_post_forms_require_csrf_and_invalid_application_url_is_rejected(client):
    assert (
        client.post(
            "/login", data={"email": "a@example.test", "password": "wrong"}
        ).status_code
        == 403
    )
    assert (
        _register(client, email="safe@example.test", company="Safe Co").status_code
        == 303
    )
    page = client.get("/post-job")
    response = client.post(
        "/post-job",
        data={
            "csrf_token": _csrf(page),
            "title": "Test role",
            "location": "Remote",
            "job_type": "full-time",
            "description": "A role",
            "apply_url": "javascript:alert(1)",
        },
    )
    assert response.status_code == 200
    assert "http or https" in response.text
    assert client.get("/pricing").status_code == 200


def test_admin_can_reject_a_pending_job(client, db_session, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin-reject@example.test")
    assert (
        _register(
            client, email="reject-owner@example.test", company="Review Co"
        ).status_code
        == 303
    )
    assert _post_job(client, title="Pending role").status_code == 303
    job = db_session.query(Job).filter_by(title="Pending role").one()
    dashboard = client.get("/employer/dashboard")
    client.post(
        "/logout", data={"csrf_token": _csrf(dashboard)}, follow_redirects=False
    )
    assert (
        _register(
            client, email="admin-reject@example.test", company="Admin"
        ).status_code
        == 303
    )
    moderation = client.get("/admin/jobs")
    response = client.post(
        f"/admin/jobs/{job.id}/moderate",
        data={
            "csrf_token": _csrf(moderation),
            "action": "reject",
            "moderation_note": "Please add salary information.",
        },
    )
    assert response.status_code == 200
    db_session.refresh(job)
    assert job.status == "closed"
    assert job.is_active is False
    assert job.moderation_note == "Please add salary information."


def test_aggregated_job_has_no_jobposting_structured_data(client, seed_jobs):
    job = seed_jobs[1]
    response = client.get(f"/jobs/{job.id}-junior-react-developer")
    assert response.status_code == 200
    assert "application/ld+json" not in response.text
