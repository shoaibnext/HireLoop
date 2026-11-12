"""Link candidate accounts to their private CV profiles."""

import sqlalchemy as sa
from alembic import op

revision = "0007_candidate_accounts"
down_revision = "0006_applications"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("cv_profiles") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_cv_profiles_user_id_users",
            "users",
            ["user_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_cv_profiles_user_id", ["user_id"], unique=True)


def downgrade():
    with op.batch_alter_table("cv_profiles") as batch_op:
        batch_op.drop_index("ix_cv_profiles_user_id")
        batch_op.drop_constraint("fk_cv_profiles_user_id_users", type_="foreignkey")
        batch_op.drop_column("user_id")
