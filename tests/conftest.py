import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from jobboard.main import app, get_db
from jobboard.models import Base, Job


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
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


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
