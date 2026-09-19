from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Shorthands used by every table below.
Id = BigInteger
TS = DateTime(timezone=True)
NOW = func.now()


class Base(DeclarativeBase):
    type_annotation_map = {datetime: TS, dict[str, Any]: JSONB}


def pk() -> Mapped[int]:
    return mapped_column(Id, Identity(), primary_key=True)


def fk(target: str, *, ondelete: str = "CASCADE", nullable: bool = False) -> Mapped[int]:
    return mapped_column(Id, ForeignKey(target, ondelete=ondelete), nullable=nullable, index=True)


# ---- Identity and household -------------------------------------------------


class Household(Base):
    __tablename__ = "households"
    id: Mapped[int] = pk()
    name: Mapped[str] = mapped_column(String(16), unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = pk()
    firebase_uid: Mapped[str] = mapped_column(String(128), unique=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str | None] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    app_version: Mapped[str | None] = mapped_column(String(32))
    household_id: Mapped[int] = fk("households.id", ondelete="RESTRICT")
    is_household_admin: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)


class HouseholdInvitation(Base):
    __tablename__ = "household_invitations"
    __table_args__ = (
        Index(
            "uq_household_invitations_pending",
            "household_id",
            "email",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
        CheckConstraint(
            "status in ('pending', 'accepted', 'rejected')", name="ck_invitation_status"
        ),
    )
    id: Mapped[int] = pk()
    household_id: Mapped[int] = fk("households.id")
    inviting_user_id: Mapped[int] = fk("users.id")
    email: Mapped[str] = mapped_column(String(320), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)
    expires_at: Mapped[datetime]


class ImpersonationLog(Base):
    __tablename__ = "impersonation_logs"
    admin_user_id: Mapped[int] = mapped_column(
        Id, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    target_user_id: Mapped[int] = mapped_column(
        Id, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    method: Mapped[str] = mapped_column(String(8), primary_key=True)
    path: Mapped[str] = mapped_column(String(512), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=1)


# ---- Learners ----------------------------------------------------------------


class LearnerType(Base):
    __tablename__ = "learner_types"
    id: Mapped[int] = pk()
    name: Mapped[str] = mapped_column(String(32), unique=True)


class Pusher(Base):
    __tablename__ = "pushers"
    __table_args__ = (
        Index("uq_pushers_household_name", "household_id", func.lower(text("name")), unique=True),
    )
    id: Mapped[int] = pk()
    household_id: Mapped[int] = fk("households.id")
    name: Mapped[str] = mapped_column(String(100))
    is_human: Mapped[bool] = mapped_column(default=False, server_default="false")
    is_hidden: Mapped[bool] = mapped_column(default=False, server_default="false")
    birth_date: Mapped[date | None] = mapped_column(Date)
    sex: Mapped[str | None] = mapped_column(String(16))
    learner_type_id: Mapped[int | None] = fk("learner_types.id", ondelete="SET NULL", nullable=True)
    sub_type: Mapped[str | None] = mapped_column(String(100))
    country: Mapped[str | None] = mapped_column(String(2))
    language: Mapped[str | None] = mapped_column(String(8))
    training_started_at: Mapped[date | None] = mapped_column(Date)
    avatar_key: Mapped[str | None] = mapped_column(String(255))
    interactions_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


# ---- Buttons and audio -------------------------------------------------------


class ButtonConcept(Base):
    __tablename__ = "button_concepts"
    id: Mapped[int] = pk()
    concept: Mapped[str] = mapped_column(String(64), unique=True)


class Audio(Base):
    __tablename__ = "audios"
    id: Mapped[int] = pk()
    household_id: Mapped[int] = fk("households.id")
    name: Mapped[str] = mapped_column(String(100))
    storage_key: Mapped[str] = mapped_column(String(255))
    crc32: Mapped[int] = mapped_column(BigInteger)
    byte_size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)


class Button(Base):
    __tablename__ = "buttons"
    __table_args__ = (
        Index(
            "uq_buttons_household_text",
            "household_id",
            func.lower(text("text")),
            unique=True,
            postgresql_where=text("deleted_at is null"),
        ),
        CheckConstraint("origin in ('app', 'connect')", name="ck_button_origin"),
    )
    id: Mapped[int] = pk()
    household_id: Mapped[int] = fk("households.id")
    text: Mapped[str] = mapped_column(String(100))
    word: Mapped[str] = mapped_column(String(100))
    normalized_word: Mapped[str] = mapped_column(String(100))
    introduced_at: Mapped[date | None] = mapped_column(Date)
    button_concept_id: Mapped[int | None] = fk(
        "button_concepts.id", ondelete="SET NULL", nullable=True
    )
    is_hidden: Mapped[bool] = mapped_column(default=False, server_default="false")
    note: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(8), default="app", server_default="app")
    audio_id: Mapped[int | None] = fk("audios.id", ondelete="SET NULL", nullable=True)
    webhook_url: Mapped[str | None] = mapped_column(String(2048))
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)


class WebhookLog(Base):
    __tablename__ = "webhook_logs"
    id: Mapped[int] = pk()
    button_id: Mapped[int] = fk("buttons.id")
    url: Mapped[str] = mapped_column(String(2048))
    status_code: Mapped[int | None] = mapped_column(Integer)
    requested_at: Mapped[datetime] = mapped_column(server_default=NOW)
    responded_at: Mapped[datetime | None]


# ---- Devices -----------------------------------------------------------------


class BaseStation(Base):
    __tablename__ = "bases"
    __table_args__ = (
        CheckConstraint("group_window_seconds between 0 and 3600", name="ck_base_group_window"),
    )
    id: Mapped[int] = pk()
    household_id: Mapped[int] = fk("households.id")
    serial_number: Mapped[str] = mapped_column(String(12), unique=True)
    name: Mapped[str | None] = mapped_column(String(100))
    default_pusher_id: Mapped[int | None] = fk("pushers.id", ondelete="SET NULL", nullable=True)
    group_window_seconds: Mapped[int] = mapped_column(Integer, default=15, server_default="15")
    fw_version: Mapped[str | None] = mapped_column(String(32))
    battery_level: Mapped[int | None] = mapped_column(Integer)
    battery_updated_at: Mapped[datetime | None]
    last_online_at: Mapped[datetime | None]
    reported_state: Mapped[dict[str, Any] | None]
    created_by_user_id: Mapped[int | None] = fk("users.id", ondelete="SET NULL", nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)


class BaseButton(Base):
    __tablename__ = "base_buttons"
    # ponytail: uniqueness of button_serial_number is per base here; per-household is enforced
    # in the link service, since a household can own several bases.
    __table_args__ = (UniqueConstraint("base_id", "button_serial_number"),)
    id: Mapped[int] = pk()
    base_id: Mapped[int] = fk("bases.id")
    button_id: Mapped[int] = mapped_column(
        Id, ForeignKey("buttons.id", ondelete="CASCADE"), unique=True
    )
    button_serial_number: Mapped[str] = mapped_column(String(32))
    battery_level: Mapped[int | None] = mapped_column(Integer)
    battery_updated_at: Mapped[datetime | None]
    last_online_at: Mapped[datetime | None]
    desired_audio_id: Mapped[int | None] = fk("audios.id", ondelete="SET NULL", nullable=True)
    desired_deleted: Mapped[bool] = mapped_column(default=False, server_default="false")
    desired_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    applied_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class DeviceEvent(Base):
    __tablename__ = "device_events"
    __table_args__ = (
        UniqueConstraint(
            "base_id",
            "event_type",
            "occurred_at",
            "button_serial_number",
            postgresql_nulls_not_distinct=True,
        ),
    )
    id: Mapped[int] = pk()
    base_id: Mapped[int] = fk("bases.id")
    button_serial_number: Mapped[str | None] = mapped_column(String(32))
    event_type: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any] | None]
    occurred_at: Mapped[datetime]
    received_at: Mapped[datetime] = mapped_column(server_default=NOW)


# ---- Interactions ------------------------------------------------------------


class Context(Base):
    __tablename__ = "contexts"
    __table_args__ = (
        Index(
            "uq_contexts_household_text",
            "household_id",
            func.lower(text("text")),
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        CheckConstraint("applies_to in ('human', 'learner', 'both')", name="ck_context_applies_to"),
    )
    id: Mapped[int] = pk()
    household_id: Mapped[int | None] = fk("households.id", nullable=True)
    text: Mapped[str] = mapped_column(String(100))
    applies_to: Mapped[str] = mapped_column(String(8), default="both", server_default="both")


class Interaction(Base):
    __tablename__ = "interactions"
    __table_args__ = (
        Index(
            "ix_interactions_feed",
            "household_id",
            text("occurred_at desc"),
            postgresql_where=text("deleted_at is null and not is_hidden"),
        ),
        CheckConstraint("origin in ('app', 'base', 'split')", name="ck_interaction_origin"),
    )
    id: Mapped[int] = pk()
    household_id: Mapped[int] = fk("households.id")
    pusher_id: Mapped[int | None] = fk("pushers.id", ondelete="SET NULL", nullable=True)
    note: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime]
    device_timezone: Mapped[str | None] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(8), default="app", server_default="app")
    is_favourite: Mapped[bool] = mapped_column(default=False, server_default="false")
    is_hidden: Mapped[bool] = mapped_column(default=False, server_default="false")
    num_presses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    duration_seconds: Mapped[float] = mapped_column(default=0, server_default="0")
    created_by_user_id: Mapped[int | None] = fk("users.id", ondelete="SET NULL", nullable=True)
    created_by_base_id: Mapped[int | None] = fk("bases.id", ondelete="SET NULL", nullable=True)
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW)


class ButtonPress(Base):
    __tablename__ = "button_presses"
    __table_args__ = (
        UniqueConstraint("interaction_id", "press_order", deferrable=True, initially="DEFERRED"),
    )
    id: Mapped[int] = pk()
    interaction_id: Mapped[int] = fk("interactions.id")
    button_id: Mapped[int] = fk("buttons.id")
    press_order: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime | None]
    reported_timestamp: Mapped[int | None] = mapped_column(BigInteger)


class InteractionContext(Base):
    __tablename__ = "interaction_contexts"
    __table_args__ = (PrimaryKeyConstraint("interaction_id", "context_id"),)
    interaction_id: Mapped[int] = fk("interactions.id")
    context_id: Mapped[int] = fk("contexts.id")


class ModeledPusher(Base):
    __tablename__ = "modeled_pushers"
    __table_args__ = (PrimaryKeyConstraint("interaction_id", "pusher_id"),)
    interaction_id: Mapped[int] = fk("interactions.id")
    pusher_id: Mapped[int] = fk("pushers.id")


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (
        Index(
            "ix_notes_feed",
            "household_id",
            text("occurred_at desc"),
            postgresql_where=text("deleted_at is null and not is_hidden"),
        ),
    )
    id: Mapped[int] = pk()
    household_id: Mapped[int] = fk("households.id")
    text: Mapped[str] = mapped_column(Text)
    occurred_at: Mapped[datetime]
    device_timezone: Mapped[str | None] = mapped_column(String(64))
    is_favourite: Mapped[bool] = mapped_column(default=False, server_default="false")
    is_hidden: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_by_user_id: Mapped[int | None] = fk("users.id", ondelete="SET NULL", nullable=True)
    deleted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)


# ---- Preferences and push ----------------------------------------------------

PREFERENCE_KEYS = frozenset(
    {"activity_sort", "button_sort", "feature_flags", "default_pusher_id", "push_frequency"}
)


class Preference(Base):
    __tablename__ = "preferences"
    user_id: Mapped[int] = mapped_column(
        Id, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[dict[str, Any] | None]


class PushToken(Base):
    __tablename__ = "push_tokens"
    token: Mapped[str] = mapped_column(String(512), primary_key=True)
    user_id: Mapped[int] = fk("users.id")
    platform: Mapped[str | None] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(server_default=NOW, onupdate=NOW)


class PushLog(Base):
    __tablename__ = "push_log"
    __table_args__ = (Index("ix_push_log_rate", "user_id", "key", "sent_at"),)
    id: Mapped[int] = pk()
    user_id: Mapped[int | None] = fk("users.id", ondelete="SET NULL", nullable=True)
    household_id: Mapped[int | None] = fk("households.id", ondelete="SET NULL", nullable=True)
    base_id: Mapped[int | None] = fk("bases.id", ondelete="SET NULL", nullable=True)
    key: Mapped[str] = mapped_column(String(32))
    sent_at: Mapped[datetime] = mapped_column(server_default=NOW)
    error: Mapped[str | None] = mapped_column(Text)


class AiLog(Base):
    """One row per successful model call: rate limiting and the spend meter."""

    __tablename__ = "ai_log"
    __table_args__ = (
        Index("ix_ai_log_user_rate", "user_id", "kind", "created_at"),
        Index("ix_ai_log_household_rate", "household_id", "kind", "created_at"),
    )
    id: Mapped[int] = pk()
    user_id: Mapped[int | None] = fk("users.id", ondelete="SET NULL", nullable=True)
    household_id: Mapped[int | None] = fk("households.id", ondelete="SET NULL", nullable=True)
    kind: Mapped[str] = mapped_column(String(16))  # chat | log_text | digest
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=NOW)
