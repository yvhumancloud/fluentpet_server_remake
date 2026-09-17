"""button_presses (interaction_id, press_order) unique deferred, so renumbering is plain updates

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

NAME = "button_presses_interaction_id_press_order_key"


def upgrade() -> None:
    op.drop_constraint(NAME, "button_presses", type_="unique")
    op.create_unique_constraint(
        NAME,
        "button_presses",
        ["interaction_id", "press_order"],
        deferrable=True,
        initially="DEFERRED",
    )


def downgrade() -> None:
    op.drop_constraint(NAME, "button_presses", type_="unique")
    op.create_unique_constraint(NAME, "button_presses", ["interaction_id", "press_order"])
