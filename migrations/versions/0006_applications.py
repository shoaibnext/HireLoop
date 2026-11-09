"""Add candidate applications for direct jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0006_applications"
down_revision = "0005_email_alerts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("employer_id", sa.Integer(), sa.ForeignKey("employers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("candidate_email", sa.String(320), nullable=False),
        sa.Column("candidate_note", sa.Text(), nullable=False),
        sa.Column("shared_skills", sa.String(2000), nullable=False, server_default=""),
        sa.Column("match_score", sa.Integer(), nullable=False),
        sa.Column("matched_skills", sa.Text(), nullable=False, server_default=""),
        sa.Column("missing_skills", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(12), nullable=False, server_default="new"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('new', 'shortlisted', 'rejected')", name="ck_applications_status"),
        sa.CheckConstraint("match_score >= 0 AND match_score <= 100", name="ck_applications_match_score"),
        sa.UniqueConstraint("job_id", "candidate_email", name="uq_applications_job_email"),
    )
    op.create_index("ix_applications_job_id", "applications", ["job_id"])
    op.create_index("ix_applications_employer_id", "applications", ["employer_id"])
    op.create_index("ix_applications_candidate_email", "applications", ["candidate_email"])
    op.create_index("ix_applications_status", "applications", ["status"])


def downgrade():
    op.drop_index("ix_applications_status", table_name="applications")
    op.drop_index("ix_applications_candidate_email", table_name="applications")
    op.drop_index("ix_applications_employer_id", table_name="applications")
    op.drop_index("ix_applications_job_id", table_name="applications")
    op.drop_table("applications")
