"""initial

Revision ID: 00bab8e4bfe3
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "button_concepts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("concept", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("concept"),
    )
    op.create_table(
        "households",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "learner_types",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "audios",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("crc32", sa.BigInteger(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audios_household_id"), "audios", ["household_id"], unique=False)
    op.create_table(
        "contexts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.String(length=100), nullable=False),
        sa.Column("applies_to", sa.String(length=8), server_default="both", nullable=False),
        sa.CheckConstraint(
            "applies_to in ('human', 'learner', 'both')", name="ck_context_applies_to"
        ),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_contexts_household_id"), "contexts", ["household_id"], unique=False)
    op.create_index(
        "uq_contexts_household_text",
        "contexts",
        ["household_id", sa.literal_column("lower(text)")],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_table(
        "pushers",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_human", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_hidden", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("sex", sa.String(length=16), nullable=True),
        sa.Column("learner_type_id", sa.BigInteger(), nullable=True),
        sa.Column("sub_type", sa.String(length=100), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column("training_started_at", sa.Date(), nullable=True),
        sa.Column("avatar_key", sa.String(length=255), nullable=True),
        sa.Column("interactions_count", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["learner_type_id"], ["learner_types.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_pushers_household_id"), "pushers", ["household_id"], unique=False)
    op.create_index(
        op.f("ix_pushers_learner_type_id"), "pushers", ["learner_type_id"], unique=False
    )
    op.create_index(
        "uq_pushers_household_name",
        "pushers",
        ["household_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("firebase_uid", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
        sa.Column("app_version", sa.String(length=32), nullable=True),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("is_household_admin", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("firebase_uid"),
    )
    op.create_index(op.f("ix_users_household_id"), "users", ["household_id"], unique=False)
    op.create_table(
        "bases",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("serial_number", sa.String(length=12), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("default_pusher_id", sa.BigInteger(), nullable=True),
        sa.Column("group_window_seconds", sa.Integer(), server_default="15", nullable=False),
        sa.Column("fw_version", sa.String(length=32), nullable=True),
        sa.Column("battery_level", sa.Integer(), nullable=True),
        sa.Column("battery_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_online_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("group_window_seconds between 0 and 3600", name="ck_base_group_window"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["default_pusher_id"], ["pushers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("serial_number"),
    )
    op.create_index(
        op.f("ix_bases_created_by_user_id"), "bases", ["created_by_user_id"], unique=False
    )
    op.create_index(
        op.f("ix_bases_default_pusher_id"), "bases", ["default_pusher_id"], unique=False
    )
    op.create_index(op.f("ix_bases_household_id"), "bases", ["household_id"], unique=False)
    op.create_table(
        "buttons",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.String(length=100), nullable=False),
        sa.Column("word", sa.String(length=100), nullable=False),
        sa.Column("normalized_word", sa.String(length=100), nullable=False),
        sa.Column("introduced_at", sa.Date(), nullable=True),
        sa.Column("button_concept_id", sa.BigInteger(), nullable=True),
        sa.Column("is_hidden", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("origin", sa.String(length=8), server_default="app", nullable=False),
        sa.Column("audio_id", sa.BigInteger(), nullable=True),
        sa.Column("webhook_url", sa.String(length=2048), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("origin in ('app', 'connect')", name="ck_button_origin"),
        sa.ForeignKeyConstraint(["audio_id"], ["audios.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["button_concept_id"], ["button_concepts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_buttons_audio_id"), "buttons", ["audio_id"], unique=False)
    op.create_index(
        op.f("ix_buttons_button_concept_id"), "buttons", ["button_concept_id"], unique=False
    )
    op.create_index(op.f("ix_buttons_household_id"), "buttons", ["household_id"], unique=False)
    op.create_index(
        "uq_buttons_household_text",
        "buttons",
        ["household_id", sa.literal_column("lower(text)")],
        unique=True,
        postgresql_where=sa.text("deleted_at is null"),
    )
    op.create_table(
        "household_invitations",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("inviting_user_id", sa.BigInteger(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('pending', 'accepted', 'rejected')", name="ck_invitation_status"
        ),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["inviting_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_household_invitations_email"), "household_invitations", ["email"], unique=False
    )
    op.create_index(
        op.f("ix_household_invitations_household_id"),
        "household_invitations",
        ["household_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_household_invitations_inviting_user_id"),
        "household_invitations",
        ["inviting_user_id"],
        unique=False,
    )
    op.create_index(
        "uq_household_invitations_pending",
        "household_invitations",
        ["household_id", "email"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "impersonation_logs",
        sa.Column("admin_user_id", sa.BigInteger(), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("path", sa.String(length=512), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(
            "admin_user_id", "target_user_id", "method", "path", "window_start"
        ),
    )
    op.create_table(
        "notes",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_timezone", sa.String(length=64), nullable=True),
        sa.Column("is_favourite", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_hidden", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_notes_created_by_user_id"), "notes", ["created_by_user_id"], unique=False
    )
    op.create_index(
        "ix_notes_feed",
        "notes",
        ["household_id", sa.literal_column("occurred_at desc")],
        unique=False,
        postgresql_where=sa.text("deleted_at is null and not is_hidden"),
    )
    op.create_index(op.f("ix_notes_household_id"), "notes", ["household_id"], unique=False)
    op.create_table(
        "preferences",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "key"),
    )
    op.create_table(
        "push_tokens",
        sa.Column("token", sa.String(length=512), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("platform", sa.String(length=16), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("token"),
    )
    op.create_index(op.f("ix_push_tokens_user_id"), "push_tokens", ["user_id"], unique=False)
    op.create_table(
        "base_buttons",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("base_id", sa.BigInteger(), nullable=False),
        sa.Column("button_id", sa.BigInteger(), nullable=False),
        sa.Column("button_serial_number", sa.String(length=32), nullable=False),
        sa.Column("battery_level", sa.Integer(), nullable=True),
        sa.Column("battery_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_online_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("desired_audio_id", sa.BigInteger(), nullable=True),
        sa.Column("desired_deleted", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("desired_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("applied_version", sa.Integer(), server_default="0", nullable=False),
        sa.ForeignKeyConstraint(["base_id"], ["bases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["button_id"], ["buttons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["desired_audio_id"], ["audios.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("base_id", "button_serial_number"),
        sa.UniqueConstraint("button_id"),
    )
    op.create_index(op.f("ix_base_buttons_base_id"), "base_buttons", ["base_id"], unique=False)
    op.create_index(
        op.f("ix_base_buttons_desired_audio_id"), "base_buttons", ["desired_audio_id"], unique=False
    )
    op.create_table(
        "device_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("base_id", sa.BigInteger(), nullable=False),
        sa.Column("button_serial_number", sa.String(length=32), nullable=True),
        sa.Column("event_type", sa.String(length=16), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["base_id"], ["bases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "base_id",
            "event_type",
            "occurred_at",
            "button_serial_number",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(op.f("ix_device_events_base_id"), "device_events", ["base_id"], unique=False)
    op.create_table(
        "interactions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("household_id", sa.BigInteger(), nullable=False),
        sa.Column("pusher_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_timezone", sa.String(length=64), nullable=True),
        sa.Column("origin", sa.String(length=8), server_default="app", nullable=False),
        sa.Column("is_favourite", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_hidden", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("num_presses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_seconds", sa.Float(), server_default="0", nullable=False),
        sa.Column("created_by_user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_base_id", sa.BigInteger(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("origin in ('app', 'base', 'split')", name="ck_interaction_origin"),
        sa.ForeignKeyConstraint(["created_by_base_id"], ["bases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pusher_id"], ["pushers.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_interactions_created_by_base_id"),
        "interactions",
        ["created_by_base_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_interactions_created_by_user_id"),
        "interactions",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        "ix_interactions_feed",
        "interactions",
        ["household_id", sa.literal_column("occurred_at desc")],
        unique=False,
        postgresql_where=sa.text("deleted_at is null and not is_hidden"),
    )
    op.create_index(
        op.f("ix_interactions_household_id"), "interactions", ["household_id"], unique=False
    )
    op.create_index(op.f("ix_interactions_pusher_id"), "interactions", ["pusher_id"], unique=False)
    op.create_table(
        "push_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("household_id", sa.BigInteger(), nullable=True),
        sa.Column("base_id", sa.BigInteger(), nullable=True),
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column(
            "sent_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["base_id"], ["bases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["household_id"], ["households.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_push_log_base_id"), "push_log", ["base_id"], unique=False)
    op.create_index(op.f("ix_push_log_household_id"), "push_log", ["household_id"], unique=False)
    op.create_index("ix_push_log_rate", "push_log", ["user_id", "key", "sent_at"], unique=False)
    op.create_index(op.f("ix_push_log_user_id"), "push_log", ["user_id"], unique=False)
    op.create_table(
        "webhook_logs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("button_id", sa.BigInteger(), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["button_id"], ["buttons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_webhook_logs_button_id"), "webhook_logs", ["button_id"], unique=False)
    op.create_table(
        "button_presses",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("interaction_id", sa.BigInteger(), nullable=False),
        sa.Column("button_id", sa.BigInteger(), nullable=False),
        sa.Column("press_order", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_timestamp", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["button_id"], ["buttons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["interaction_id"], ["interactions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("interaction_id", "press_order"),
    )
    op.create_index(
        op.f("ix_button_presses_button_id"), "button_presses", ["button_id"], unique=False
    )
    op.create_index(
        op.f("ix_button_presses_interaction_id"), "button_presses", ["interaction_id"], unique=False
    )
    op.create_table(
        "interaction_contexts",
        sa.Column("interaction_id", sa.BigInteger(), nullable=False),
        sa.Column("context_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["context_id"], ["contexts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["interaction_id"], ["interactions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("interaction_id", "context_id"),
    )
    op.create_index(
        op.f("ix_interaction_contexts_context_id"),
        "interaction_contexts",
        ["context_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_interaction_contexts_interaction_id"),
        "interaction_contexts",
        ["interaction_id"],
        unique=False,
    )
    op.create_table(
        "modeled_pushers",
        sa.Column("interaction_id", sa.BigInteger(), nullable=False),
        sa.Column("pusher_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["interaction_id"], ["interactions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pusher_id"], ["pushers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("interaction_id", "pusher_id"),
    )
    op.create_index(
        op.f("ix_modeled_pushers_interaction_id"),
        "modeled_pushers",
        ["interaction_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_modeled_pushers_pusher_id"), "modeled_pushers", ["pusher_id"], unique=False
    )

    # seeds
    op.bulk_insert(
        sa.table("learner_types", sa.column("name")), [{"name": n} for n in ("dog", "cat", "other")]
    )
    op.bulk_insert(
        sa.table("button_concepts", sa.column("concept")),
        [
            {"concept": c}
            for c in [
                "ALL DONE",
                "ANIMAL-BIRD",
                "ANIMAL-BUG",
                "ANIMAL-CAT",
                "ANIMAL-DOG",
                "ANIMAL-FRIEND",
                "ANIMAL-LIZARD",
                "ANIMAL-MOUSE",
                "ANIMAL-OTHER",
                "ANIMAL-RABBIT",
                "ANIMAL-SQUIRREL",
                "BACK UP",
                "BATH",
                "BEGIN TRAINING SESSION",
                "BRUSH",
                "BYE",
                "CAREFUL",
                "COME",
                "CONCERNED",
                "CRATE",
                "CUDDLE",
                "DOWN",
                "DROP IT",
                "FIND",
                "FOOD",
                "GENTLE",
                "GET IT",
                "GIVE",
                "GO INSIDE",
                "GO OUTSIDE",
                "GO TO BED",
                "HAPPY",
                "HEEL",
                "HELP",
                "HI",
                "HOME",
                "HUG",
                "INDOOR LOCATION-BATHROOM",
                "INDOOR LOCATION-BEDROOM",
                "INDOOR LOCATION-DOWNSTAIRS",
                "INDOOR LOCATION-KITCHEN",
                "INDOOR LOCATION-OFFICE",
                "INDOOR LOCATION-OTHER",
                "INDOOR LOCATION-UPSTAIRS",
                "INTERROGATIVE/QUESTION",
                "JUMP",
                "KISS",
                "LAP",
                "LATER",
                "LEAVE IT",
                "LET'S GO",
                "LOAD",
                "LOOK",
                "LOVE YOU",
                "MAD",
                "MORE",
                "NO",
                "NOW",
                "OFF",
                "OK",
                "OTHER",
                "OUTDOOR LOCATION-BACK YARD",
                "OUTDOOR LOCATION-DRIVEWAY",
                "OUTDOOR LOCATION-FRONT YARD",
                "OUTDOOR LOCATION-GARDEN",
                "OUTDOOR LOCATION-OTHER",
                "OUTDOOR LOCATION-PORCH",
                "OWN NAME",
                "PEE",
                "PERSON-FRIEND",
                "PERSON-NEIGHBOR",
                "PERSON-OTHER",
                "PERSON-PARENT",
                "PERSON-RELATIVE",
                "PERSON-ROOMMATE",
                "PERSON-SIBLING",
                "PERSON-STRANGER",
                "PERSON-WALKER/TRAINER",
                "PLACE-BEACH",
                "PLACE-DAYCARE",
                "PLACE-OTHER",
                "PLACE-PARK",
                "PLACE-SOMEONE ELSE'S HOUSE",
                "PLACE-VET",
                "PLAY-ALL",
                "PLAY-FETCH",
                "PLAY-HIDE & SEEK",
                "PLAY-TUG",
                "POOP",
                "POTTY",
                "QUIET",
                "RIDE",
                "RUN/GO FAST",
                "SAD",
                "SCRITCHES",
                "SETTLE",
                "SHAKE OFF",
                "SIT",
                "STAY",
                "SWIM",
                "TEMPERATURE-COLD",
                "TEMPERATURE-HOT",
                "TOY-BALL",
                "TOY-BONE",
                "TOY-FRISBEE",
                "TOY-OTHER",
                "TOY-PUZZLE",
                "TOY-STUFFED ANIMAL",
                "TREAT-ALL",
                "TREAT-CHICKEN",
                "TREAT-ICE",
                "TRICK-BEG",
                "TRICK-BOW",
                "TRICK-CRAWL",
                "TRICK-DANCE",
                "TRICK-FIGURE EIGHT",
                "TRICK-GIVE PAW",
                "TRICK-HIGH FIVE",
                "TRICK-OTHER",
                "TRICK-PLAY DEAD",
                "TRICK-ROLL OVER",
                "TRICK-SHOW BELLY",
                "TRICK-SPEAK",
                "TRICK-SPIN",
                "TRICK-STAND",
                "TRICK-TOUCH",
                "WAIT",
                "WALK",
                "WANT",
                "WATER",
                "YES",
            ]
        ],
    )
    op.bulk_insert(
        sa.table("contexts", sa.column("text"), sa.column("applies_to")),
        [
            {"text": t, "applies_to": a}
            for t, a in [
                ("Accidental Interaction", "both"),
                ("Asking Question", "both"),
                ("Attention-Seeking", "both"),
                ("Experiment", "both"),
                ("Frustration", "both"),
                ("Inform", "both"),
                ("Modeled", "human"),
                ("Narrate", "both"),
                ("Nobody Home", "learner"),
                ("Not Sure", "learner"),
                ("Other", "both"),
                ("Request Action/Object", "both"),
                ("Speak for Others", "both"),
                ("Speak to Other Learner", "learner"),
                ("Studying Buttons", "both"),
                ("Share Thoughts/Feelings", "both"),
            ]
        ],
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    op.drop_index(op.f("ix_modeled_pushers_pusher_id"), table_name="modeled_pushers")
    op.drop_index(op.f("ix_modeled_pushers_interaction_id"), table_name="modeled_pushers")
    op.drop_table("modeled_pushers")
    op.drop_index(op.f("ix_interaction_contexts_interaction_id"), table_name="interaction_contexts")
    op.drop_index(op.f("ix_interaction_contexts_context_id"), table_name="interaction_contexts")
    op.drop_table("interaction_contexts")
    op.drop_index(op.f("ix_button_presses_interaction_id"), table_name="button_presses")
    op.drop_index(op.f("ix_button_presses_button_id"), table_name="button_presses")
    op.drop_table("button_presses")
    op.drop_index(op.f("ix_webhook_logs_button_id"), table_name="webhook_logs")
    op.drop_table("webhook_logs")
    op.drop_index(op.f("ix_push_log_user_id"), table_name="push_log")
    op.drop_index("ix_push_log_rate", table_name="push_log")
    op.drop_index(op.f("ix_push_log_household_id"), table_name="push_log")
    op.drop_index(op.f("ix_push_log_base_id"), table_name="push_log")
    op.drop_table("push_log")
    op.drop_index(op.f("ix_interactions_pusher_id"), table_name="interactions")
    op.drop_index(op.f("ix_interactions_household_id"), table_name="interactions")
    op.drop_index(
        "ix_interactions_feed",
        table_name="interactions",
        postgresql_where=sa.text("deleted_at is null and not is_hidden"),
    )
    op.drop_index(op.f("ix_interactions_created_by_user_id"), table_name="interactions")
    op.drop_index(op.f("ix_interactions_created_by_base_id"), table_name="interactions")
    op.drop_table("interactions")
    op.drop_index(op.f("ix_device_events_base_id"), table_name="device_events")
    op.drop_table("device_events")
    op.drop_index(op.f("ix_base_buttons_desired_audio_id"), table_name="base_buttons")
    op.drop_index(op.f("ix_base_buttons_base_id"), table_name="base_buttons")
    op.drop_table("base_buttons")
    op.drop_index(op.f("ix_push_tokens_user_id"), table_name="push_tokens")
    op.drop_table("push_tokens")
    op.drop_table("preferences")
    op.drop_index(op.f("ix_notes_household_id"), table_name="notes")
    op.drop_index(
        "ix_notes_feed",
        table_name="notes",
        postgresql_where=sa.text("deleted_at is null and not is_hidden"),
    )
    op.drop_index(op.f("ix_notes_created_by_user_id"), table_name="notes")
    op.drop_table("notes")
    op.drop_table("impersonation_logs")
    op.drop_index(
        "uq_household_invitations_pending",
        table_name="household_invitations",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index(
        op.f("ix_household_invitations_inviting_user_id"), table_name="household_invitations"
    )
    op.drop_index(op.f("ix_household_invitations_household_id"), table_name="household_invitations")
    op.drop_index(op.f("ix_household_invitations_email"), table_name="household_invitations")
    op.drop_table("household_invitations")
    op.drop_index(
        "uq_buttons_household_text",
        table_name="buttons",
        postgresql_where=sa.text("deleted_at is null"),
    )
    op.drop_index(op.f("ix_buttons_household_id"), table_name="buttons")
    op.drop_index(op.f("ix_buttons_button_concept_id"), table_name="buttons")
    op.drop_index(op.f("ix_buttons_audio_id"), table_name="buttons")
    op.drop_table("buttons")
    op.drop_index(op.f("ix_bases_household_id"), table_name="bases")
    op.drop_index(op.f("ix_bases_default_pusher_id"), table_name="bases")
    op.drop_index(op.f("ix_bases_created_by_user_id"), table_name="bases")
    op.drop_table("bases")
    op.drop_index(op.f("ix_users_household_id"), table_name="users")
    op.drop_table("users")
    op.drop_index("uq_pushers_household_name", table_name="pushers")
    op.drop_index(op.f("ix_pushers_learner_type_id"), table_name="pushers")
    op.drop_index(op.f("ix_pushers_household_id"), table_name="pushers")
    op.drop_table("pushers")
    op.drop_index(
        "uq_contexts_household_text", table_name="contexts", postgresql_nulls_not_distinct=True
    )
    op.drop_index(op.f("ix_contexts_household_id"), table_name="contexts")
    op.drop_table("contexts")
    op.drop_index(op.f("ix_audios_household_id"), table_name="audios")
    op.drop_table("audios")
    op.drop_table("learner_types")
    op.drop_table("households")
    op.drop_table("button_concepts")
    # ### end Alembic commands ###
