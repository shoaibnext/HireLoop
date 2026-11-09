"""CV storage and job match cache."""

import sqlalchemy as sa
from alembic import op

revision = "0004_cv_matching"
down_revision = "0003_employer_flow"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cv_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_key_hash", sa.String(64), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("skills", sa.String(2000), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_cv_profiles_session_key_hash", "cv_profiles", ["session_key_hash"], unique=True
    )
    op.create_index("ix_cv_profiles_expires_at", "cv_profiles", ["expires_at"])
    op.create_table(
        "match_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cv_id", sa.Integer(), sa.ForeignKey("cv_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("matched_skills", sa.Text(), nullable=False, server_default=""),
        sa.Column("missing_skills", sa.Text(), nullable=False, server_default=""),
        sa.Column("why", sa.String(500), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("score >= 0 AND score <= 100", name="ck_match_cache_score"),
        sa.UniqueConstraint("cv_id", "job_id", name="uq_match_cache_cv_job"),
    )
    op.create_index("ix_match_cache_cv_id", "match_cache", ["cv_id"])
    op.create_index("ix_match_cache_job_id", "match_cache", ["job_id"])


def downgrade():
    op.drop_table("match_cache")
    op.drop_table("cv_profiles")
