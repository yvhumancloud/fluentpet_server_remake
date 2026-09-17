from collections import Counter
from datetime import date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.auth import CurrentUser, DbSession
from app.errors import ApiError
from app.models import Button, ButtonPress, Context, Interaction, InteractionContext
from app.routers.pushers import get_pusher
from app.schemas import (
    ButtonRef,
    Combination,
    DayStat,
    HourStat,
    PusherStatsOut,
    StatsRange,
    StatsSummaryOut,
    StatsTotals,
    TextCount,
)

router = APIRouter(tags=["stats"])
MAX_DAYS = 183


def _ranked(counter: Counter) -> list[TextCount]:
    return [
        TextCount(text=t, count=n) for t, n in sorted(counter.items(), key=lambda x: (-x[1], x[0]))
    ]


@router.get("/stats/summary")
async def summary(
    pusher_id: int,
    from_: Annotated[date, Query(alias="from")],
    to: date,
    user: CurrentUser,
    session: DbSession,
) -> StatsSummaryOut:
    if to < from_ or (to - from_).days > MAX_DAYS:
        raise ApiError(f"range must be 0–{MAX_DAYS} days", code="validation_error", status=422)
    pusher = await get_pusher(session, user, pusher_id)
    tz = ZoneInfo(user.timezone)
    today = datetime.now(tz).date()
    start = datetime.combine(from_, datetime.min.time(), tz)
    end = datetime.combine(to + timedelta(days=1), datetime.min.time(), tz)
    mine = select(Interaction.id).where(
        Interaction.pusher_id == pusher.id,
        Interaction.household_id == user.household_id,
        Interaction.deleted_at.is_(None),
    )
    presses_of = select(ButtonPress).where(ButtonPress.interaction_id.in_(mine))

    n_interactions = await session.scalar(select(func.count()).select_from(mine.subquery()))
    n_presses, n_buttons = (
        await session.execute(
            presses_of.with_only_columns(
                func.count(), func.count(func.distinct(ButtonPress.button_id))
            )
        )
    ).one()
    first = await session.scalar(
        select(func.min(Interaction.occurred_at)).where(Interaction.id.in_(mine))
    )

    in_range = mine.where(Interaction.occurred_at >= start, Interaction.occurred_at < end)
    rows = (
        await session.execute(
            select(Interaction.id, Interaction.occurred_at, Button.text, Button.created_at)
            .join(ButtonPress, ButtonPress.interaction_id == Interaction.id)
            .join(Button, Button.id == ButtonPress.button_id)
            .where(Interaction.id.in_(in_range))
        )
    ).all()
    logged, created, per_day_presses = Counter(), Counter(), Counter()
    per_day_ids, per_hour_ids, phrases = {}, {}, {}
    for iid, at, text, button_created in rows:
        local = at.astimezone(tz)
        logged[text] += 1
        if start <= button_created < end:
            created[text] += 1
        per_day_presses[local.date()] += 1
        per_day_ids.setdefault(local.date(), set()).add(iid)
        per_hour_ids.setdefault(local.hour, set()).add(iid)
        phrases.setdefault(iid, []).append(text)
    combos = Counter(", ".join(sorted(p)) for p in phrases.values() if len(p) > 1)
    contexts = Counter(
        await session.scalars(
            select(Context.text)
            .join(InteractionContext, InteractionContext.context_id == Context.id)
            .where(InteractionContext.interaction_id.in_(in_range))
        )
    )
    return StatsSummaryOut(
        pusher=pusher,
        days_since_training_started=(today - pusher.training_started_at).days
        if pusher.training_started_at
        else None,
        days_since_first_interaction=(today - first.astimezone(tz).date()).days if first else None,
        totals=StatsTotals(
            interactions=n_interactions, presses=n_presses, distinct_buttons=n_buttons
        ),
        range=StatsRange(
            from_=from_,
            to=to,
            buttons_logged=_ranked(logged),
            buttons_created=_ranked(created),
            combinations=_ranked(combos),
            contexts=_ranked(contexts),
            per_day=[
                DayStat(date=d, presses=per_day_presses[d], interactions=len(per_day_ids[d]))
                for d in sorted(per_day_ids)
            ],
            per_hour=[
                HourStat(hour=h, interactions=len(per_hour_ids[h])) for h in sorted(per_hour_ids)
            ],
        ),
    )


@router.get("/pushers/{pusher_id}/stats")
async def pusher_stats(pusher_id: int, user: CurrentUser, session: DbSession) -> PusherStatsOut:
    pusher = await get_pusher(session, user, pusher_id)
    mine = select(Interaction.id).where(
        Interaction.pusher_id == pusher.id, Interaction.deleted_at.is_(None)
    )
    rows = (
        await session.execute(
            select(ButtonPress.interaction_id, Button.id, Button.text)
            .join(Button, Button.id == ButtonPress.button_id)
            .where(ButtonPress.interaction_id.in_(mine))
        )
    ).all()
    by_text = Counter(text for _, _, text in rows)
    ranked = _ranked(by_text)
    phrases: dict[int, list[tuple[int, str]]] = {}
    for iid, bid, text in rows:
        phrases.setdefault(iid, []).append((bid, text))
    combos = Counter(tuple(sorted(p, key=lambda x: x[1])) for p in phrases.values() if len(p) > 1)
    top = combos.most_common(1)
    contexts = Counter(
        await session.scalars(
            select(Context.text)
            .join(InteractionContext, InteractionContext.context_id == Context.id)
            .where(InteractionContext.interaction_id.in_(mine))
        )
    )
    first = await session.scalar(
        select(func.min(Interaction.occurred_at)).where(Interaction.id.in_(mine))
    )
    tz = ZoneInfo(user.timezone)
    return PusherStatsOut(
        most_pressed=ranked[:5],
        least_pressed=sorted(ranked, key=lambda c: (c.count, c.text))[:5],
        top_contexts=_ranked(contexts)[:10],
        most_frequent_combination=Combination(
            buttons=[ButtonRef(id=i, text=t) for i, t in top[0][0]], count=top[0][1]
        )
        if top
        else None,
        days_since_first_entry=(datetime.now(tz).date() - first.astimezone(tz).date()).days
        if first
        else None,
    )
