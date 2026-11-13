"""M7 direct-job applications and owner-only applicant review."""

import re
from datetime import UTC, datetime, timedelta
from io import BytesIO

from docx import Document

from jobboard.models import Application, Employer, Job, User


def csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, html
    return match.group(1)


def docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def register(register_account, email: str, company: str):
    return register_account(email=email, company=company)


def login(client, email: str):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"csrf_token": csrf(page.text), "email": email, "password": "correct horse battery staple"},
        follow_redirects=False,
    )


def test_application_consent_scoring_owner_review_and_status_updates(client, db_session, register_account):
    assert register(register_account, "owner@example.org", "Example Systems").status_code == 303
    user = db_session.query(User).filter_by(email="owner@example.org").one()
    employer = db_session.query(Employer).filter_by(user_id=user.id).one()
    job = Job(
        source="direct",
        external_id="m7-python-role",
        employer_id=employer.id,
        title="Senior Python Engineer",
        company=employer.company_name,
        location="Lahore",
        skills="python,fastapi,postgresql",
        level="senior",
        status="approved",
        expires_at=datetime.now(UTC) + timedelta(days=20),
        apply_url="https://example.org/apply",
    )
    db_session.add(job)
    db_session.commit()

    detail = client.get(f"/jobs/{job.id}-senior-python-engineer")
    assert "Apply through Kaamly.pk" in detail.text
    apply_page = client.get(f"/jobs/{job.id}/apply")
    assert "Upload a CV before applying" in apply_page.text
    no_csrf = client.post(f"/jobs/{job.id}/apply", data={"email": "candidate@example.net"})
    assert no_csrf.status_code == 403
    consent_csrf = csrf(client.get("/upload-cv").text)
    no_cv = client.post(
        f"/jobs/{job.id}/apply",
        data={"csrf_token": consent_csrf, "email": "candidate@example.net", "note": "I build Python services.", "share_skills": "1"},
    )
    assert "Upload a CV before applying" in no_cv.text

    upload_page = client.get("/upload-cv")
    uploaded = client.post(
        "/upload-cv",
        data={"csrf_token": csrf(upload_page.text)},
        files={"cv_file": ("candidate.docx", docx_bytes("Senior Python engineer with FastAPI"), "application/octet-stream")},
        follow_redirects=False,
    )
    assert uploaded.status_code == 303
    application_page = client.get(f"/jobs/{job.id}/apply")
    assert "Your original CV file and extracted CV text are not sent." in application_page.text
    assert "I agree to share my email address, short note, and recognized CV skills" in application_page.text
    submitted = client.post(
        f"/jobs/{job.id}/apply",
        data={
            "csrf_token": csrf(application_page.text),
            "email": "Candidate@example.net",
            "note": "I have built Python APIs and would like to discuss this role.",
            "share_skills": "1",
        },
        follow_redirects=False,
    )
    assert submitted.status_code == 303
    assert submitted.headers["location"].endswith("?application=sent")
    confirmation = client.get(submitted.headers["location"])
    assert "Your application was sent" in confirmation.text

    application = db_session.query(Application).one()
    assert application.candidate_email == "candidate@example.net"
    assert application.shared_skills == "python,fastapi"
    assert (application.match_score, application.matched_skills, application.missing_skills) == (67, "python,fastapi", "postgresql")
    assert "Senior Python engineer with FastAPI" not in str(application.__dict__)
    duplicate = client.post(
        f"/jobs/{job.id}/apply",
        data={
            "csrf_token": csrf(client.get(f"/jobs/{job.id}/apply").text),
            "email": "candidate@example.net",
            "note": "Another note.",
            "share_skills": "1",
        },
    )
    assert duplicate.status_code == 409
    assert db_session.query(Application).count() == 1

    client.cookies.clear()
    assert login(client, "owner@example.org").status_code == 303
    dashboard = client.get("/employer/dashboard")
    assert "Review applications (1)" in dashboard.text
    review = client.get("/employer/applications")
    assert "candidate@example.net" in review.text
    assert "67% match" in review.text
    assert "Matched skills:</span> python,fastapi" in review.text
    assert "mailto:candidate@example.net" in review.text
    saved = client.post(
        f"/employer/applications/{application.id}/status",
        data={"csrf_token": csrf(review.text), "status": "shortlisted"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    db_session.refresh(application)
    assert application.status == "shortlisted"

    client.cookies.clear()
    assert register(register_account, "other@example.org", "Other Company").status_code == 303
    other_review = client.get("/employer/applications")
    assert "candidate@example.net" not in other_review.text
    forbidden = client.post(
        f"/employer/applications/{application.id}/status",
        data={"csrf_token": csrf(client.get("/post-job").text), "status": "rejected"},
    )
    assert forbidden.status_code == 403
    db_session.refresh(application)
    assert application.status == "shortlisted"


def test_only_active_approved_direct_jobs_accept_applications(client, db_session):
    aggregated = Job(source="remoteok", external_id="m7-remote", title="Engineer", company="Remote", skills="python")
    pending_direct = Job(source="direct", external_id="m7-pending", title="Pending role", company="Company", status="pending", skills="python")
    db_session.add_all([aggregated, pending_direct])
    db_session.commit()
    assert client.get(f"/jobs/{aggregated.id}/apply").status_code == 404
    assert client.get(f"/jobs/{pending_direct.id}/apply").status_code == 404


def test_employer_applications_require_sign_in(client):
    response = client.get("/employer/applications", follow_redirects=False)
    assert response.status_code == 401
