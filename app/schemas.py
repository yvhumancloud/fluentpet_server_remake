import ipaddress
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)

from app.services import storage


def _iana(tz: str) -> str:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError("unknown IANA timezone") from e
    return tz


Timezone = Annotated[str, AfterValidator(_iana)]


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- me ----------------------------------------------------------------------


class MePatch(BaseModel):
    full_name: str | None = None
    timezone: Timezone | None = None
    app_version: str | None = None


class PusherOut(Out):
    id: int
    name: str
    is_human: bool
    is_hidden: bool
    avatar_key: str | None = Field(default=None, exclude=True)

    @computed_field
    @property
    def avatar_url(self) -> str | None:
        return storage.presigned_get(self.avatar_key) if self.avatar_key else None


class HouseholdOut(Out):
    id: int
    name: str


class InvitationOut(Out):
    id: int
    household_id: int
    household_name: str
    invited_by: str
    email: str
    status: str
    expires_at: datetime


class UserOut(Out):
    id: int
    email: str
    full_name: str | None
    timezone: str
    app_version: str | None
    is_household_admin: bool


class MeOut(UserOut):
    household: HouseholdOut
    pushers: list[PusherOut]
    feature_flags: dict[str, Any]
    pending_invitations: list[InvitationOut]


class HouseholdDetailOut(HouseholdOut):
    members: list[UserOut]
    invitations: list[InvitationOut]


class InvitationCreate(BaseModel):
    email: Annotated[str, Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)]


class InvitationsOut(BaseModel):
    sent: list[InvitationOut]
    received: list[InvitationOut]


# ---- pushers -----------------------------------------------------------------


class PusherIn(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)]
    is_human: bool = False
    birth_date: date | None = None
    sex: Annotated[str, Field(max_length=16)] | None = None
    learner_type_id: int | None = None
    sub_type: Annotated[str, Field(max_length=100)] | None = None
    country: Annotated[str, Field(min_length=2, max_length=2)] | None = None
    language: Annotated[str, Field(max_length=8)] | None = None
    training_started_at: date | None = None


class PusherPatch(PusherIn):
    name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    is_human: bool | None = None
    is_hidden: bool | None = None


class PusherDetailOut(PusherOut):
    birth_date: date | None
    sex: str | None
    learner_type_id: int | None
    sub_type: str | None
    country: str | None
    language: str | None
    training_started_at: date | None
    interactions_count: int


class LearnerTypeOut(Out):
    id: int
    name: str


# ---- preferences -------------------------------------------------------------

PUSH_FREQUENCIES = ("all", "on_interaction", "none")


class PreferenceIn(BaseModel):
    value: Any


class PreferenceOut(BaseModel):
    key: str
    value: Any


# ---- push tokens -------------------------------------------------------------


class PushTokenIn(BaseModel):
    token: Annotated[str, Field(min_length=1, max_length=512)]
    platform: Annotated[str, Field(max_length=16)] | None = None


# ---- buttons -----------------------------------------------------------------

BUTTON_SORTS = ("alphabet", "frequency", "date")


def _public_http(url: str) -> str:
    """Webhooks are fetched by our server: keep them off loopback, private and metadata hosts."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise ValueError("must be an http(s) URL")
    if host in ("localhost", "metadata.google.internal") or host.endswith(".internal"):
        raise ValueError("host not allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return url
    if not ip.is_global:
        raise ValueError("host not allowed")
    return url


HttpUrl = Annotated[str, Field(max_length=2048), AfterValidator(_public_http)]


class ButtonCreate(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=100)]
    note: str | None = None
    introduced_at: date | None = None
    button_concept_id: int | None = None


class ButtonPatch(ButtonCreate):
    text: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    is_hidden: bool | None = None
    audio_id: int | None = None
    webhook_url: HttpUrl | None = None


class BaseButtonOut(Out):
    id: int
    base_id: int
    button_serial_number: str
    battery_level: int | None
    battery_updated_at: datetime | None
    last_online_at: datetime | None
    desired_audio_id: int | None
    desired_deleted: bool
    desired_version: int
    applied_version: int


class ButtonOut(Out):
    id: int
    text: str
    word: str
    normalized_word: str
    introduced_at: date | None
    button_concept_id: int | None
    is_hidden: bool
    note: str | None
    origin: str
    audio_id: int | None
    webhook_url: str | None
    created_at: datetime
    base_button: BaseButtonOut | None
    press_count: int


class ButtonMerge(BaseModel):
    source_id: int
    target_id: int

    @model_validator(mode="after")
    def _distinct(self):
        if self.source_id == self.target_id:
            raise ValueError("source_id and target_id must differ")
        return self


class ButtonConceptOut(Out):
    id: int
    concept: str


# ---- audios ------------------------------------------------------------------


class AudioOut(Out):
    id: int
    household_id: int
    name: str
    crc32: int
    byte_size: int
    created_at: datetime


class UrlOut(BaseModel):
    url: str


# ---- bases -------------------------------------------------------------------

Serial = Annotated[str, Field(pattern=r"^[A-Za-z0-9]{12}$")]


class BaseCreate(BaseModel):
    serial_number: Serial
    name: Annotated[str, Field(min_length=1, max_length=100)]


class BasePatch(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    default_pusher_id: int | None = None
    group_window_seconds: Annotated[int, Field(ge=0, le=3600)] | None = None


class LinkedButtonOut(BaseButtonOut):
    button_id: int
    text: str


class BaseOut(Out):
    id: int
    serial_number: str
    name: str | None
    default_pusher_id: int | None
    group_window_seconds: int
    fw_version: str | None
    battery_level: int | None
    battery_updated_at: datetime | None
    last_online_at: datetime | None
    reported_state: dict[str, Any] | None
    created_at: datetime
    buttons: list[LinkedButtonOut]


# ---- device ------------------------------------------------------------------

EVENT_TYPES = ("press", "button_seen", "battery", "power", "fully_charged", "online")


def _epoch(v: Any) -> Any:
    """Bases report epoch numbers; ≥ 2×10⁹ means milliseconds (PRD §7)."""
    if isinstance(v, str) and v.strip().lstrip("-").replace(".", "", 1).isdigit():
        v = float(v)
    if isinstance(v, int | float) and not isinstance(v, bool):
        return datetime.fromtimestamp(v / 1000 if v >= 2_000_000_000 else v, tz=UTC)
    return v


class DeviceEventIn(BaseModel):
    serial_number: Serial
    button_serial_number: Annotated[str, Field(max_length=32)] | None = None
    type: Literal["press", "button_seen", "battery", "power", "fully_charged", "online"]
    occurred_at: Annotated[datetime, BeforeValidator(_epoch)]
    payload: dict[str, Any] | None = None


class DeviceStateIn(BaseModel):
    reported_state: dict[str, Any]
    fw_version: Annotated[str, Field(max_length=32)] | None = None
    battery_level: Annotated[int, Field(ge=0, le=100)] | None = None


class DesiredAudio(BaseModel):
    id: int
    url: str
    crc32: int


class DesiredOut(BaseModel):
    base_button_id: int
    serial_number: str
    button_serial_number: str
    desired_deleted: bool
    desired_version: int
    applied_version: int
    audio: DesiredAudio | None


class AckIn(BaseModel):
    base_button_id: int
    applied_version: Annotated[int, Field(ge=0)]


class AudioUrlIn(BaseModel):
    serial_number: Serial
    audio_id: int


class AudioUrlOut(BaseModel):
    url: str
    crc32: int


class DeviceEventResult(BaseModel):
    status: Literal["created", "duplicate", "unknown_base", "invalid"]


# ---- contexts ----------------------------------------------------------------


class ContextOut(Out):
    id: int
    household_id: int | None
    text: str
    applies_to: str


class ContextCreate(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=100)]
    applies_to: Literal["human", "learner", "both"] = "both"

    @model_validator(mode="after")
    def _trim(self):
        self.text = " ".join(self.text.split())
        if not self.text:
            raise ValueError("text is blank")
        return self


# ---- notes -------------------------------------------------------------------


class NoteIn(BaseModel):
    text: Annotated[str, Field(min_length=1)]
    occurred_at: datetime
    device_timezone: Timezone | None = None
    is_favourite: bool = False

    @model_validator(mode="after")
    def _trim(self):
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("text is blank")
        return self


class NotePatch(BaseModel):
    text: Annotated[str, Field(min_length=1)] | None = None
    occurred_at: datetime | None = None
    device_timezone: Timezone | None = None
    is_favourite: bool | None = None
    is_hidden: bool | None = None

    @model_validator(mode="after")
    def _trim(self):
        if self.text is not None:
            self.text = self.text.strip()
            if not self.text:
                raise ValueError("text is blank")
        return self


class NoteOut(Out):
    type: Literal["note"] = "note"
    id: int
    text: str
    occurred_at: datetime
    device_timezone: str | None
    is_favourite: bool
    is_hidden: bool
    created_at: datetime


# ---- interactions ------------------------------------------------------------


def _clean_note(v: str | None) -> str | None:
    return (v or "").strip() or None


class InteractionIn(BaseModel):
    pusher_id: int | None = None
    note: Annotated[str | None, AfterValidator(_clean_note)] = None
    occurred_at: datetime
    device_timezone: Timezone | None = None
    is_favourite: bool = False
    button_ids: list[int] = []
    context_ids: list[int] = []
    modeled_pusher_ids: list[int] = []


class InteractionPatch(BaseModel):
    pusher_id: int | None = None
    note: Annotated[str | None, AfterValidator(_clean_note)] = None
    occurred_at: datetime | None = None
    device_timezone: Timezone | None = None
    is_favourite: bool | None = None
    is_hidden: bool | None = None
    button_ids: list[int] | None = None
    context_ids: list[int] | None = None
    modeled_pusher_ids: list[int] | None = None


class PressOut(Out):
    id: int
    button_id: int
    text: str
    press_order: int
    occurred_at: datetime | None


class InteractionOut(Out):
    type: Literal["interaction"] = "interaction"
    id: int
    pusher_id: int | None
    pusher: PusherOut | None
    note: str | None
    occurred_at: datetime
    device_timezone: str | None
    origin: str
    is_favourite: bool
    is_hidden: bool
    num_presses: int
    duration_seconds: float
    presses: list[PressOut]
    contexts: list[ContextOut]
    modeled_pushers: list[PusherOut]
    created_by_base_id: int | None
    created_at: datetime
    updated_at: datetime


class SearchFilters(BaseModel):
    pusher_ids: list[int] = []
    context_ids: list[int] = []
    button_ids: list[int] = []
    base_ids: list[int] = []
    match: Literal["any", "all"] = "any"
    text: str | None = None
    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    notes: Literal["include", "only", "exclude"] = "include"
    with_note: Literal["include", "only", "exclude"] = "include"
    favourites: Literal["include", "only", "exclude"] = "include"
    presses: Literal["all", "single", "multiple"] = "all"
    include_hidden: bool = False

    @property
    def interactions_only(self) -> bool:
        """Interaction-shaped filters drop notes out of the feed."""
        return bool(
            self.pusher_ids
            or self.context_ids
            or self.button_ids
            or self.base_ids
            or self.notes == "exclude"
            or self.with_note == "only"
            or self.presses != "all"
        )


class SearchIn(BaseModel):
    page: Annotated[int, Field(ge=1)] = 1
    per_page: Annotated[int, Field(ge=1, le=200)] = 45
    sort: Literal["occurred_at_desc", "occurred_at_asc", "created_at_desc"] = "occurred_at_desc"
    tab: Literal["all", "assigned", "unassigned"] = "all"
    filters: SearchFilters = SearchFilters()


class InteractionMerge(BaseModel):
    interaction_ids: Annotated[list[int], Field(min_length=1)]


class BulkIn(BaseModel):
    operation: Literal["assign", "delete", "merge"]
    ids: list[int] | None = None
    all: bool = False
    pusher_id: int | None = None

    @model_validator(mode="after")
    def _target(self):
        if self.all == bool(self.ids):
            raise ValueError("pass either ids or all: true")
        if self.operation == "assign" and self.pusher_id is None:
            raise ValueError("assign needs pusher_id")
        return self


class BulkOut(BaseModel):
    affected: int
    id: int | None


class SearchCounts(BaseModel):
    communication: int
    modeling: int
    unassigned: int


class SearchOut(BaseModel):
    items: list[InteractionOut | NoteOut]
    total: int
    page: int
    per_page: int
    counts: SearchCounts


# ---- stats -------------------------------------------------------------------


class PusherRef(Out):
    id: int
    name: str


class TextCount(BaseModel):
    text: str
    count: int


class DayStat(BaseModel):
    date: date
    presses: int
    interactions: int


class HourStat(BaseModel):
    hour: int
    interactions: int


class StatsRange(BaseModel):
    from_: date = Field(serialization_alias="from")
    to: date
    buttons_logged: list[TextCount]
    buttons_created: list[TextCount]
    combinations: list[TextCount]
    contexts: list[TextCount]
    per_day: list[DayStat]
    per_hour: list[HourStat]


class StatsTotals(BaseModel):
    interactions: int
    presses: int
    distinct_buttons: int


class StatsSummaryOut(BaseModel):
    pusher: PusherRef
    days_since_training_started: int | None
    days_since_first_interaction: int | None
    totals: StatsTotals
    range: StatsRange


class ButtonRef(Out):
    id: int
    text: str


class Combination(BaseModel):
    buttons: list[ButtonRef]
    count: int


class PusherStatsOut(BaseModel):
    most_pressed: list[TextCount]
    least_pressed: list[TextCount]
    top_contexts: list[TextCount]
    most_frequent_combination: Combination | None
    days_since_first_entry: int | None


class WebhookLogOut(Out):
    id: int
    url: str
    status_code: int | None
    requested_at: datetime
    responded_at: datetime | None


# ---- ai ----------------------------------------------------------------------


class LogTextIn(BaseModel):
    text: Annotated[str, Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def _trim(self):
        self.text = self.text.strip()
        if not self.text:
            raise ValueError("text is blank")
        return self


class LogTextOut(BaseModel):
    draft: InteractionIn
    unmatched_words: list[str]
