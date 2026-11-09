"""Add double opt-in email alert subscriptions."""

import sqlalchemy as sa
from alembic import op

revision = "0005_email_alerts"
down_revision = "0004_cv_matching"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "alert_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("skills", sa.String(2000), nullable=False),
        sa.Column("location", sa.String(100), nullable=False, server_default=""),
        sa.Column("work_mode", sa.String(10), nullable=False, server_default="any"),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("confirm_token_hash", sa.String(64)),
        sa.Column("confirm_expires_at", sa.DateTime(timezone=True)),
        sa.Column("unsubscribe_salt", sa.String(32), nullable=False),
        sa.Column("unsubscribe_token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True)),
        sa.Column("last_digest_sent_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'unsubscribed')",
            name="ck_alert_subscriptions_status",
        ),
        sa.CheckConstraint(
            "work_mode IN ('any', 'remote', 'local')",
            name="ck_alert_subscriptions_work_mode",
        ),
    )
    op.create_index("ix_alert_subscriptions_email", "alert_subscriptions", ["email"], unique=True)
    op.create_index("ix_alert_subscriptions_status", "alert_subscriptions", ["status"])
    op.create_index("ix_alert_subscriptions_confirm_token_hash", "alert_subscriptions", ["confirm_token_hash"], unique=True)
    op.create_index("ix_alert_subscriptions_unsubscribe_token_hash", "alert_subscriptions", ["unsubscribe_token_hash"], unique=True)


def downgrade():
    op.drop_index("ix_alert_subscriptions_unsubscribe_token_hash", table_name="alert_subscriptions")
    op.drop_index("ix_alert_subscriptions_confirm_token_hash", table_name="alert_subscriptions")
    op.drop_index("ix_alert_subscriptions_status", table_name="alert_subscriptions")
    op.drop_index("ix_alert_subscriptions_email", table_name="alert_subscriptions")
    op.drop_table("alert_subscriptions")
