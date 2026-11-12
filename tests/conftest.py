import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from jobboard.main import app, get_db
from jobboard.models import Base, Job
from jobboard.rate_limit import limiter


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def client(db_session):
    limiter.reset()
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def register_account(client, monkeypatch):
    """Register and OTP-verify an account using a captured mock email code."""
    from jobboard import employer as employer_routes

    sent_codes = {}

    def capture_otp(user, code):
        sent_codes[user.email] = code

    monkeypatch.setattr(employer_routes, "_send_email_otp", capture_otp)

    def register(email, company="", website="https://example.org"):
        signup_page = client.get("/signup")
        signup_csrf = re.search(
            r'name="csrf_token" value="([^"]+)"', signup_page.text
        ).group(1)
        signup = client.post(
            "/signup",
            data={
                "csrf_token": signup_csrf,
                "email": email,
                "password": "correct horse battery staple",
                "account_type": "employer" if company else "candidate",
                "company_name": company,
                "website": website,
            },
            follow_redirects=False,
        )
        if signup.status_code != 303 or signup.headers.get("location") != "/verify-email":
            return signup
        verify_page = client.get("/verify-email")
        verify_csrf = re.search(
            r'name="csrf_token" value="([^"]+)"', verify_page.text
        ).group(1)
        return client.post(
            "/verify-email",
            data={"csrf_token": verify_csrf, "code": sent_codes[email]},
            follow_redirects=False,
        )

    return register


@pytest.fixture
def seed_jobs(db_session):
    jobs = [
        Job(
            source="direct",
            external_id="d1",
            title="Senior Python Engineer",
            company="One",
            location="Lahore",
            is_remote=True,
            salary="PKR 200k",
            job_type="full-time",
            tags="python",
            level="senior",
            skills="python,fastapi",
            description="<p>Build APIs</p>",
        ),
        Job(
            source="remoteok",
            external_id="r1",
            title="Junior React Developer",
            company="Two",
            location="Remote",
            is_remote=True,
            job_type="contract",
            source_url="https://remoteok.com/jobs/1",
            description="<script>alert(1)</script><p>React role</p>",
            level="junior",
            skills="react,javascript",
        ),
        Job(
            source="remotive",
            external_id="m1",
            title="Data Engineer",
            company="Three",
            location="Karachi",
            is_remote=False,
            job_type="full-time",
            source_url="https://remotive.com/jobs/1",
            description="<p>SQL role</p>",
            level="unknown",
            skills="sql",
        ),
    ]
    db_session.add_all(jobs)
    db_session.commit()
    return jobs
