"""One digest per household per week: a push and a note in the feed.

Triggered by hand: POST /internal/weekly-digest (X-Job-Key) or python -m app.jobs.weekly_digest
"""

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import typer
from sqlalchemy import func, select

from app.db import SessionLocal
from app.errors import ApiError
from app.models import AiLog, Interaction, Note, Pusher, User
from app.routers.stats import summary
from app.services import ai, push

log = logging.getLogger("fluentpet.jobs")
MIN_INTERACTIONS = 3
PREFIX = "Weekly digest — "


async def run() -> dict[str, int]:
    """Households with ≥ 3 interactions in the last 7 days and no digest in the last 6."""
    async with SessionLocal() as session:
        done = select(AiLog.household_id).where(
            AiLog.kind == "digest", AiLog.created_at > func.now() - timedelta(days=6)
        )
        households = list(
            await session.scalars(
                select(Interaction.household_id)
                .where(
                    Interaction.deleted_at.is_(None),
                    Interaction.occurred_at > func.now() - timedelta(days=7),
                    Interaction.household_id.not_in(done),
                )
                .group_by(Interaction.household_id)
                .having(func.count() >= MIN_INTERACTIONS)
            )
        )
    sent = skipped = 0
    for hid in households:
        try:
            text = await _digest(hid)
        except ApiError as e:  # model down: retry that household on the next run
            log.warning("weekly_digest: household %d skipped: %s", hid, e.message)
            text = None
        if text is None:
            skipped += 1
            continue
        await push.notify_household(hid, "weekly_digest", text=text)
        sent += 1
    log.info("weekly_digest: %d sent, %d skipped", sent, skipped)
    return {"sent": sent, "skipped": skipped}


async def _digest(household_id: int) -> str | None:
    async with SessionLocal() as session, session.begin():
        user = await session.scalar(
            select(User)
            .where(User.household_id == household_id)
            .order_by(User.is_household_admin.desc(), User.id)
        )
        learners = list(
            await session.scalars(
                select(Pusher).where(
                    Pusher.household_id == household_id, ~Pusher.is_human, ~Pusher.is_hidden
                )
            )
        )
        if user is None or not learners:
            return None
        today = datetime.now(ZoneInfo(user.timezone)).date()
        stats = []
        for p in learners:
            this_week = await summary(p.id, today - timedelta(days=6), today, user, session)
            if not this_week.range.per_day:
                continue
            last_week = await summary(
                p.id, today - timedelta(days=13), today - timedelta(days=7), user, session
            )
            stats.append(
                {
                    "name": p.name,
                    "this_week": ai.trim_stats(this_week).range.model_dump(mode="json"),
                    "last_week": ai.trim_stats(last_week).range.model_dump(mode="json"),
                }
            )
        if not stats:
            return None
        text = await ai.digest(session, user, stats)
        session.add(
            Note(
                household_id=household_id,
                text=PREFIX + text,
                occurred_at=datetime.now(ZoneInfo(user.timezone)),
                device_timezone=user.timezone,
            )
        )
        return text


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    typer.run(main)
