"""Add employer accounts, direct job workflow, and moderation/payment fields."""

import sqlalchemy as sa
from alembic import op

revision = "0003_employer_flow"
down_revision = "0002_ingestion_hardening"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "role", sa.String(length=20), nullable=False, server_default="employer"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])
    op.create_table(
        "employers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=False),
        sa.Column("website", sa.String(length=2048), nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_employers_user_id"),
    )
    op.create_index("ix_employers_user_id", "employers", ["user_id"])

    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("employer_id", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "status",
                sa.String(length=10),
                nullable=False,
                server_default="approved",
            )
        )
        batch_op.add_column(
            sa.Column("featured_until", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "featured_requested",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column("apply_url", sa.String(length=2048), nullable=True)
        )
        batch_op.add_column(
            sa.Column("apply_email", sa.String(length=320), nullable=True)
        )
        batch_op.add_column(sa.Column("salary_min", sa.Numeric(12, 2), nullable=True))
        batch_op.add_column(sa.Column("salary_max", sa.Numeric(12, 2), nullable=True))
        batch_op.add_column(
            sa.Column("salary_currency", sa.String(length=3), nullable=True)
        )
        batch_op.add_column(
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "is_paid", sa.Boolean(), nullable=False, server_default=sa.false()
            )
        )
        batch_op.add_column(sa.Column("payment_note", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("moderation_note", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_jobs_employer_id_employers",
            "employers",
            ["employer_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_check_constraint(
            "ck_jobs_status",
            "status IN ('draft', 'pending', 'approved', 'closed', 'expired')",
        )
        batch_op.create_check_constraint(
            "ck_jobs_salary_currency",
            "salary_currency IS NULL OR salary_currency IN ('PKR', 'USD')",
        )
    op.create_index("ix_jobs_employer_id", "jobs", ["employer_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])


def downgrade():
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_employer_id", table_name="jobs")
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_constraint("ck_jobs_salary_currency", type_="check")
        batch_op.drop_constraint("ck_jobs_status", type_="check")
        batch_op.drop_constraint("fk_jobs_employer_id_employers", type_="foreignkey")
        for column in (
            "moderation_note",
            "payment_note",
            "is_paid",
            "expires_at",
            "salary_currency",
            "salary_max",
            "salary_min",
            "apply_email",
            "apply_url",
            "featured_requested",
            "featured_until",
            "status",
            "employer_id",
        ):
            batch_op.drop_column(column)
    op.drop_index("ix_employers_user_id", table_name="employers")
    op.drop_table("employers")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
