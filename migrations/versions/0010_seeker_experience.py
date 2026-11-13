"""Persist seeker saves, private job state, preferences, and recent searches."""

import sqlalchemy as sa
from alembic import op

revision = "0010_seeker_experience"
down_revision = "0009_google_oauth"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "seeker_job_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_saved", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("application_status", sa.String(16)),
        sa.Column("private_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "job_id", name="uq_seeker_job_state_user_job"),
        sa.CheckConstraint(
            "application_status IS NULL OR application_status IN ('applied', 'interviewing', 'offer', 'rejected')",
            name="ck_seeker_job_state_status",
        ),
    )
    op.create_index("ix_seeker_job_states_user_id", "seeker_job_states", ["user_id"])
    op.create_index("ix_seeker_job_states_job_id", "seeker_job_states", ["job_id"])
    op.create_table(
        "seeker_preferences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("target_roles", sa.String(1000), nullable=False, server_default=""),
        sa.Column("skills", sa.String(2000), nullable=False, server_default=""),
        sa.Column("location", sa.String(100), nullable=False, server_default=""),
        sa.Column("remote_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("minimum_salary", sa.Numeric(12, 2)),
        sa.Column("salary_currency", sa.String(3)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_seeker_preferences_user_id", "seeker_preferences", ["user_id"])
    op.create_table(
        "recent_searches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("query_string", sa.String(2000), nullable=False),
        sa.Column("searched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recent_searches_user_id", "recent_searches", ["user_id"])


def downgrade():
    op.drop_index("ix_recent_searches_user_id", table_name="recent_searches")
    op.drop_table("recent_searches")
    op.drop_index("ix_seeker_preferences_user_id", table_name="seeker_preferences")
    op.drop_table("seeker_preferences")
    op.drop_index("ix_seeker_job_states_job_id", table_name="seeker_job_states")
    op.drop_index("ix_seeker_job_states_user_id", table_name="seeker_job_states")
    op.drop_table("seeker_job_states")
