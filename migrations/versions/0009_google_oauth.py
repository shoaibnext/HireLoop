"""Store the stable Google OpenID subject for social sign-in."""

import sqlalchemy as sa
from alembic import op

revision = "0009_google_oauth"
down_revision = "0008_email_otp_verification"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("google_sub", sa.String(255), nullable=True))
    op.create_index("ix_users_google_sub", "users", ["google_sub"], unique=True)


def downgrade():
    op.drop_index("ix_users_google_sub", table_name="users")
    op.drop_column("users", "google_sub")
