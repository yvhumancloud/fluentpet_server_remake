"""Claude calls: vocabulary prompt, rate limit, ai_log. `client()` is the boundary tests replace."""

import logging
import time
from datetime import UTC, datetime, timedelta
from functools import cache
from zoneinfo import ZoneInfo

import anthropic
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import AiLog, Button, Context, Pusher, User
from app.schemas import InteractionIn
from app.settings import settings

log = logging.getLogger("fluentpet.ai")
DAILY_LIMIT = {"chat": 30, "log_text": 50}  # per user, rolling 24 h


class AiUnavailable(ApiError):
    status = 503
    code = "ai_unavailable"


class RateLimited(ApiError):
    status = 429
    code = "rate_limited"


@cache
def client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=30, max_retries=1)


async def check_limit(session: AsyncSession, user: User, kind: str) -> int:
    """Raises 429 at the daily limit; returns how many calls remain after this one."""
    used = await session.scalar(
        select(func.count()).where(
            AiLog.user_id == user.id,
            AiLog.kind == kind,
            AiLog.created_at > func.now() - timedelta(hours=24),
        )
    )
    if used >= DAILY_LIMIT[kind]:
        raise RateLimited(f"{DAILY_LIMIT[kind]} {kind} calls per day")
    return DAILY_LIMIT[kind] - used - 1


async def call(session: AsyncSession, user: User, kind: str, request):
    """Runs `request(client)`; one ai_log row per successful call. A failure is a 503 and rolls
    the request back, so it is only logged (the row would go with the rollback anyway)."""
    if not settings.anthropic_api_key:
        raise AiUnavailable("AI is not configured")
    started = time.perf_counter()
    try:
        response = await request(client())
    except anthropic.APIError as e:
        log.warning("claude %s failed: %s", kind, e)
        raise AiUnavailable("AI is unavailable, try again later") from e
    session.add(
        AiLog(
            user_id=user.id,
            household_id=user.household_id,
            kind=kind,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=response.usage.cache_read_input_tokens or 0,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    )
    return response


class Vocabulary(BaseModel):
    pushers: dict[int, Pusher]
    buttons: dict[int, Button]
    contexts: dict[int, Context]
    model_config = {"arbitrary_types_allowed": True}

    def prompt(self, user: User) -> str:
        now = datetime.now(ZoneInfo(user.timezone))
        return "\n".join(
            [
                f"Now: {now.isoformat(timespec='minutes')} ({user.timezone}).",
                f"The user is {user.full_name or user.email.split('@')[0]}.",
                "Pushers (id: name, kind):",
                *(
                    f"  {p.id}: {p.name}, {'human' if p.is_human else 'learner'}"
                    for p in self.pushers.values()
                ),
                "Buttons (id: text):",
                *(f"  {b.id}: {b.text}" for b in self.buttons.values()),
                "Contexts (id: text, applies to):",
                *(f"  {c.id}: {c.text}, {c.applies_to}" for c in self.contexts.values()),
            ]
        )


async def vocabulary(session: AsyncSession, user: User) -> Vocabulary:
    hid = user.household_id
    pushers = await session.scalars(
        select(Pusher).where(Pusher.household_id == hid, ~Pusher.is_hidden).order_by(Pusher.name)
    )
    buttons = await session.scalars(
        select(Button)
        .where(Button.household_id == hid, Button.deleted_at.is_(None), ~Button.is_hidden)
        .order_by(Button.text)
    )
    contexts = await session.scalars(
        select(Context)
        .where(or_(Context.household_id.is_(None), Context.household_id == hid))
        .order_by(Context.text)
    )
    return Vocabulary(
        pushers={p.id: p for p in pushers},
        buttons={b.id: b for b in buttons},
        contexts={c.id: c for c in contexts},
    )


# ---- log by text -------------------------------------------------------------

LOG_TEXT_SYSTEM = """The user describes in free text something a learner (a pet) or a person said \
with sound buttons. Extract exactly one interaction.
- button_ids: the buttons pressed, in the order pressed, matched by text; close synonyms count \
(walkies -> Walk). Words the learner said that have no button go to unmatched_words.
- pusher_id: who pressed. "I", "me", "we" mean the human pusher named like the user. Null when \
unclear.
- context_ids: only contexts the text clearly states.
- occurred_at: ISO 8601 with offset, resolved against Now and the timezone ("this morning", \
"at 8", "yesterday"). Null when the text gives no time.
- note: anything else worth keeping, in the user's words. Null if nothing.
"""


class LogTextDraft(BaseModel):
    pusher_id: int | None
    button_ids: list[int]
    context_ids: list[int]
    occurred_at: str | None
    note: str | None
    unmatched_words: list[str]


async def log_text(session: AsyncSession, user: User, text: str) -> tuple[InteractionIn, list[str]]:
    vocab = await vocabulary(session, user)
    response = await call(
        session,
        user,
        "log_text",
        lambda c: c.messages.parse(
            model=settings.ai_model,
            max_tokens=2048,
            system=LOG_TEXT_SYSTEM + vocab.prompt(user),
            messages=[{"role": "user", "content": text}],
            output_format=LogTextDraft,
            output_config={"effort": "low"},
        ),
    )
    d: LogTextDraft = response.parsed_output
    try:
        occurred_at = datetime.fromisoformat(d.occurred_at or "")
    except ValueError:
        occurred_at = datetime.now(UTC)
    if occurred_at.tzinfo is None:  # the model dropped the offset: it meant the user's clock
        occurred_at = occurred_at.replace(tzinfo=ZoneInfo(user.timezone))
    draft = InteractionIn(
        pusher_id=d.pusher_id if d.pusher_id in vocab.pushers else None,
        note=d.note,
        occurred_at=occurred_at.astimezone(UTC),
        device_timezone=user.timezone,
        button_ids=[b for b in d.button_ids if b in vocab.buttons],
        context_ids=[c for c in d.context_ids if c in vocab.contexts],
    )
    return draft, d.unmatched_words
