"""Claude calls: vocabulary prompt, rate limit, ai_log. `client()` is the boundary tests replace."""

import json
import logging
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as time_
from functools import cache
from zoneinfo import ZoneInfo

import anthropic
from anthropic import beta_async_tool
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import AiLog, Button, Context, Pusher, User
from app.schemas import InteractionIn, SearchFilters, SearchIn, StatsSummaryOut
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
    return anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key.strip() or None,
        auth_token=settings.anthropic_auth_token.strip() or None,
        base_url=settings.anthropic_base_url.strip() or None,
        timeout=30,
        max_retries=1,
    )


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


async def call(
    session: AsyncSession, kind: str, request, *, household_id: int, user_id: int | None = None
):
    """Runs `request(client)` (one response, or the list of turns a tool runner produced) and
    writes one ai_log row for the successful call, in the caller's transaction. A failure is a
    503 that rolls the request back, so it is only logged. Returns the last response."""
    if not settings.ai_configured:  # SSM cannot store an empty value; blank = off
        raise AiUnavailable("AI is not configured")
    started = time.perf_counter()
    try:
        responses = await request(client())
    except anthropic.APIError as e:
        log.warning("claude %s failed: %s", kind, e)
        raise AiUnavailable("AI is unavailable, try again later") from e
    if not isinstance(responses, list):
        responses = [responses]
    session.add(
        AiLog(
            user_id=user_id,
            household_id=household_id,
            kind=kind,
            model=responses[-1].model,
            input_tokens=sum(r.usage.input_tokens for r in responses),
            output_tokens=sum(r.usage.output_tokens for r in responses),
            cache_read_tokens=sum(r.usage.cache_read_input_tokens or 0 for r in responses),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    )
    return responses[-1]


class Vocabulary(BaseModel):
    pushers: dict[int, Pusher]
    buttons: dict[int, Button]
    contexts: dict[int, Context]
    model_config = {"arbitrary_types_allowed": True}

    def prompt(self, user: User) -> str:
        """The household in text. No clock here: this block is cached across chat turns."""
        return "\n".join(
            [
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


def now_line(user: User) -> str:
    now = datetime.now(ZoneInfo(user.timezone))
    return f"Now: {now.isoformat(timespec='minutes')} ({user.timezone})."


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


def parse_json_text(response, model: type[BaseModel]) -> BaseModel:
    """The reply as `model`, tolerating a ```json fence around it."""
    text = text_of(response).removeprefix("```json").removeprefix("```").removesuffix("```")
    return model.model_validate_json(text.strip())


async def log_text(session: AsyncSession, user: User, text: str) -> tuple[InteractionIn, list[str]]:
    vocab = await vocabulary(session, user)
    system = f"{LOG_TEXT_SYSTEM}{vocab.prompt(user)}\n{now_line(user)}"
    if settings.ai_is_claude:
        request = lambda c: c.messages.parse(  # noqa: E731
            model=settings.ai_model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": text}],
            output_format=LogTextDraft,
            output_config={"effort": "low"},
        )
    else:
        schema = json.dumps(LogTextDraft.model_json_schema()["properties"])
        request = lambda c: c.messages.create(  # noqa: E731
            model=settings.ai_model,
            max_tokens=2048,
            system=f"{system}\nReply with one JSON object and nothing else, fields: {schema}",
            messages=[{"role": "user", "content": text}],
        )
    response = await call(
        session, "log_text", request, household_id=user.household_id, user_id=user.id
    )
    d: LogTextDraft = (
        response.parsed_output if settings.ai_is_claude else parse_json_text(response, LogTextDraft)
    )
    if d is None:  # Claude answered without a parsable object (refusal or cut off)
        raise AiUnavailable("AI gave no usable answer, try again")
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


# ---- chat --------------------------------------------------------------------

CHAT_SYSTEM = """You are the FluentPet assistant for one household. People teach their pets \
(learners) to communicate by pressing sound buttons; every press sequence is logged as an \
interaction with a pusher, a time and optional contexts and a note.
- Answer questions about presses, buttons and patterns from the tools only; never invent presses. \
When a tool returns nothing, say so.
- "This week" means the last 7 days, "today" the current date, unless the user says otherwise. \
Dates are YYYY-MM-DD in the user's timezone.
- Reply in under 120 words, plain text, in the user's language.
- Anything outside this household's data (vet or training advice, other households): say briefly \
that it is outside what you can see.
"""
CHAT_MAX_TOOL_ROUNDS = 5
SEARCH_LIMIT = 50


def trim_stats(out: StatsSummaryOut) -> StatsSummaryOut:
    """Ranked lists cut to 10: enough for an answer, small enough for a prompt."""
    r = out.range
    for name in ("buttons_logged", "buttons_created", "combinations", "contexts"):
        setattr(r, name, getattr(r, name)[:10])
    return out


def text_of(response) -> str:
    return "".join(b.text for b in response.content if b.type == "text").strip()


def chat_tools(session: AsyncSession, user: User):
    """The two tools, closed over the caller: household scoping is the same as the endpoints'."""
    from app.routers.search import search
    from app.routers.stats import summary

    @beta_async_tool
    async def stats_summary(pusher_id: int, from_date: str, to_date: str) -> str:
        """Button statistics for one pusher over a date range: totals, buttons logged and created,
        combinations, contexts, per day and per hour of day.

        Args:
            pusher_id: id from the pushers list.
            from_date: first day, YYYY-MM-DD.
            to_date: last day, YYYY-MM-DD; at most 183 days after from_date.
        """
        try:
            out = await summary(
                pusher_id, date.fromisoformat(from_date), date.fromisoformat(to_date), user, session
            )
        except (ApiError, ValueError) as e:
            return f"error: {getattr(e, 'message', e)}"
        return trim_stats(out).model_dump_json(exclude={"pusher": {"avatar_url"}})

    @beta_async_tool
    async def search_interactions(
        pusher_id: int | None = None,
        button_ids: list[int] | None = None,
        context_ids: list[int] | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        text: str | None = None,
        limit: int = 20,
    ) -> str:
        """The most recent interactions and notes matching the filters, newest first, one per
        line: `date time pusher: BUTTON, BUTTON [contexts] "note"`.

        Args:
            pusher_id: only this pusher.
            button_ids: interactions containing any of these buttons.
            context_ids: interactions with any of these contexts.
            from_date: on or after this day, YYYY-MM-DD.
            to_date: on or before this day, YYYY-MM-DD.
            text: note text contains this.
            limit: rows to return, at most 50.
        """
        tz = ZoneInfo(user.timezone)
        try:
            body = SearchIn(
                per_page=min(limit, SEARCH_LIMIT),
                filters=SearchFilters(
                    pusher_ids=[pusher_id] if pusher_id else [],
                    button_ids=button_ids or [],
                    context_ids=context_ids or [],
                    text=text,
                    **{"from": datetime.combine(date.fromisoformat(from_date), time_.min, tz)}
                    if from_date
                    else {},
                    to=datetime.combine(date.fromisoformat(to_date), time_.max, tz)
                    if to_date
                    else None,
                ),
            )
            out = await search(body, user, session)
        except (ApiError, ValueError, ValidationError) as e:
            return f"error: {getattr(e, 'message', e)}"
        lines = [f"{out.total} matching, showing {len(out.items)}"]
        for i in out.items:
            when = i.occurred_at.astimezone(tz).strftime("%Y-%m-%d %H:%M")
            if i.type == "note":
                lines.append(f'{when} note: "{i.text}"')
                continue
            who = i.pusher.name if i.pusher else "unassigned"
            line = f"{when} {who}: " + ", ".join(p.text.upper() for p in i.presses)
            if i.contexts:
                line += " [" + ", ".join(c.text for c in i.contexts) + "]"
            if i.note:
                line += f' "{i.note}"'
            lines.append(line)
        return "\n".join(lines)

    return [stats_summary, search_interactions]


async def chat(session: AsyncSession, user: User, messages: list[dict]) -> str:
    vocab = await vocabulary(session, user)
    stable, clock = CHAT_SYSTEM + vocab.prompt(user), now_line(user)
    if settings.ai_is_claude:  # the stable block is cached across turns; the clock is not
        extra = {
            "system": [
                {"type": "text", "text": stable, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": clock},
            ],
            "output_config": {"effort": "low"},
        }
    else:
        extra = {"system": f"{stable}\n{clock}"}

    async def run(c):
        runner = c.beta.messages.tool_runner(
            model=settings.ai_model,
            max_tokens=2048,
            tools=chat_tools(session, user),
            messages=messages,
            max_iterations=CHAT_MAX_TOOL_ROUNDS + 1,
            **extra,
        )
        return [m async for m in runner]

    final = await call(session, "chat", run, household_id=user.household_id, user_id=user.id)
    return text_of(final)


# ---- weekly digest -------------------------------------------------------------

DIGEST_SYSTEM = """Write the weekly digest for a FluentPet household: what each learner (pet) said \
with their sound buttons this week, compared with last week. At most 3 sentences, plain text, \
warm but factual. Name the learner, give the top button with its count, the usual time of day if \
the per-hour data shows one, one change versus last week, and any button first pressed this week \
(buttons_created). Only state what is in the data."""


async def digest(session: AsyncSession, user: User, learners_stats: list[dict]) -> str:
    response = await call(
        session,
        "digest",
        lambda c: c.messages.create(
            model=settings.ai_model,
            max_tokens=1024,
            system=DIGEST_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(learners_stats)}],
            **({"output_config": {"effort": "medium"}} if settings.ai_is_claude else {}),
        ),
        household_id=user.household_id,
    )
    return text_of(response)
