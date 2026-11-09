"""Initial job schema matching the M1 baseline."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "source", sa.String(length=40), nullable=False, server_default="direct"
        ),
        sa.Column(
            "external_id", sa.String(length=255), nullable=False, server_default=""
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("company", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("location", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("is_remote", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "job_type", sa.String(length=40), nullable=False, server_default="full-time"
        ),
        sa.Column("salary", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("tags", sa.String(length=1000), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "source_url", sa.String(length=2048), nullable=False, server_default=""
        ),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("source", "external_id", name="uq_jobs_source_external_id"),
    )
    op.create_index("ix_jobs_source", "jobs", ["source"])
    op.create_index("ix_jobs_title", "jobs", ["title"])


def downgrade():
    op.drop_index("ix_jobs_title", table_name="jobs")
    op.drop_index("ix_jobs_source", table_name="jobs")
    op.drop_table("jobs")
