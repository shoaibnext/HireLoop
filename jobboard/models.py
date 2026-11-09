"""SQLAlchemy models and database session setup."""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_jobs_source_external_id"),
        CheckConstraint(
            "level IN ('junior', 'mid', 'senior', 'unknown')",
            name="ck_jobs_level",
        ),
        CheckConstraint(
            "status IN ('draft', 'pending', 'approved', 'closed', 'expired')",
            name="ck_jobs_status",
        ),
        CheckConstraint(
            "salary_currency IS NULL OR salary_currency IN ('PKR', 'USD')",
            name="ck_jobs_salary_currency",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(
        String(40), nullable=False, default="direct", index=True
    )
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    company: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    location: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    is_remote: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    job_type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="full-time"
    )
    salary: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    tags: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, index=True
    )
    level: Mapped[str] = mapped_column(
        String(10), nullable=False, default="unknown", index=True
    )
    skills: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    employer_id: Mapped[int | None] = mapped_column(
        ForeignKey("employers.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="approved", index=True
    )
    featured_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    featured_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    apply_url: Mapped[str | None] = mapped_column(String(2048))
    apply_email: Mapped[str | None] = mapped_column(String(320))
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payment_note: Mapped[str | None] = mapped_column(Text)
    moderation_note: Mapped[str | None] = mapped_column(Text)


class IngestRun(Base):
    __tablename__ = "ingest_runs"
    __table_args__ = (
        CheckConstraint("status IN ('ok', 'failed')", name="ck_ingest_runs_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="ok")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="employer")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class Employer(Base):
    __tablename__ = "employers"
    __table_args__ = (UniqueConstraint("user_id", name="uq_employers_user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    website: Mapped[str] = mapped_column(String(2048), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class CVProfile(Base):
    __tablename__ = "cv_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    skills: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class MatchCache(Base):
    __tablename__ = "match_cache"
    __table_args__ = (
        UniqueConstraint("cv_id", "job_id", name="uq_match_cache_cv_job"),
        CheckConstraint("score >= 0 AND score <= 100", name="ck_match_cache_score"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cv_id: Mapped[int] = mapped_column(ForeignKey("cv_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    matched_skills: Mapped[str] = mapped_column(Text, nullable=False, default="")
    missing_skills: Mapped[str] = mapped_column(Text, nullable=False, default="")
    why: Mapped[str] = mapped_column(String(500), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AlertSubscription(Base):
    __tablename__ = "alert_subscriptions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'unsubscribed')",
            name="ck_alert_subscriptions_status",
        ),
        CheckConstraint(
            "work_mode IN ('any', 'remote', 'local')",
            name="ck_alert_subscriptions_work_mode",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    skills: Mapped[str] = mapped_column(String(2000), nullable=False)
    location: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    work_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="any")
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="pending", index=True)
    confirm_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    confirm_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unsubscribe_salt: Mapped[str] = mapped_column(String(32), nullable=False)
    unsubscribe_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_digest_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
