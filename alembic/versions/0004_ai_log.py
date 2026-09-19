"""ai_log: one row per model call

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ai_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "household_id", sa.BigInteger(), sa.ForeignKey("households.id", ondelete="SET NULL")
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_ai_log_user_id", "ai_log", ["user_id"])
    op.create_index("ix_ai_log_household_id", "ai_log", ["household_id"])
    op.create_index("ix_ai_log_user_rate", "ai_log", ["user_id", "kind", "created_at"])
    op.create_index("ix_ai_log_household_rate", "ai_log", ["household_id", "kind", "created_at"])


def downgrade() -> None:
    op.drop_table("ai_log")
