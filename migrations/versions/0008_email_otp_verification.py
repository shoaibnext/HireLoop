"""Add expiring email OTP verification to new accounts."""

import sqlalchemy as sa
from alembic import op

revision = "0008_email_otp_verification"
down_revision = "0007_candidate_accounts"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("email_verification_hash", sa.String(64)))
    op.add_column("users", sa.Column("email_verification_expires_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("email_verification_sent_at", sa.DateTime(timezone=True)))
    op.add_column(
        "users",
        sa.Column("email_verification_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    # Existing accounts predate verification and remain usable.
    op.execute("UPDATE users SET email_verified_at = created_at WHERE email_verified_at IS NULL")


def downgrade():
    op.drop_column("users", "email_verification_attempts")
    op.drop_column("users", "email_verification_sent_at")
    op.drop_column("users", "email_verification_expires_at")
    op.drop_column("users", "email_verification_hash")
    op.drop_column("users", "email_verified_at")
