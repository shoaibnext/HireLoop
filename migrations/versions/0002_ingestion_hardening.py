"""Add ingestion tracking, activity, level, and normalized skills."""

import sqlalchemy as sa
from alembic import op

revision = "0002_ingestion_hardening"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "jobs",
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "jobs",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "level", sa.String(length=10), nullable=False, server_default="unknown"
        ),
    )
    op.add_column(
        "jobs",
        sa.Column("skills", sa.String(length=2000), nullable=False, server_default=""),
    )
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.create_check_constraint(
            "ck_jobs_level", "level IN ('junior', 'mid', 'senior', 'unknown')"
        )
    op.create_index("ix_jobs_last_seen_at", "jobs", ["last_seen_at"])
    op.create_index("ix_jobs_is_active", "jobs", ["is_active"])
    op.create_index("ix_jobs_level", "jobs", ["level"])
    op.create_table(
        "ingest_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("new", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="ok"),
        sa.CheckConstraint("status IN ('ok', 'failed')", name="ck_ingest_runs_status"),
    )
    op.create_index("ix_ingest_runs_source", "ingest_runs", ["source"])


def downgrade():
    op.drop_index("ix_ingest_runs_source", table_name="ingest_runs")
    op.drop_table("ingest_runs")
    op.drop_index("ix_jobs_level", table_name="jobs")
    op.drop_index("ix_jobs_is_active", table_name="jobs")
    op.drop_index("ix_jobs_last_seen_at", table_name="jobs")
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_constraint("ck_jobs_level", type_="check")
    op.drop_column("jobs", "skills")
    op.drop_column("jobs", "level")
    op.drop_column("jobs", "is_active")
    op.drop_column("jobs", "last_seen_at")
