"""Tell households about bases that went quiet.

Triggered by hand: POST /internal/base-offline (X-Job-Key) or python -m app.jobs.base_offline
"""

import asyncio
import logging
from datetime import timedelta

import typer
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import BaseStation, PushLog
from app.services import push

log = logging.getLogger("fluentpet.jobs")
MIN, MAX = timedelta(hours=36), timedelta(hours=168)


async def run() -> int:
    """Push `base_offline` once per outage: last seen 36 h–168 h ago, no push since then."""
    async with SessionLocal() as session:
        since = select(func.max(PushLog.sent_at)).where(
            PushLog.base_id == BaseStation.id, PushLog.key == "base_offline"
        )
        bases = list(
            await session.scalars(
                select(BaseStation).where(
                    BaseStation.last_online_at.between(func.now() - MAX, func.now() - MIN),
                    func.coalesce(since.scalar_subquery(), BaseStation.last_online_at)
                    <= BaseStation.last_online_at,
                )
            )
        )
    for base in bases:
        await push.notify_household(
            base.household_id,
            "base_offline",
            base_id=base.id,
            base=base.name or base.serial_number,
        )
    log.info("base_offline: %d pushed", len(bases))
    return len(bases)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    typer.run(main)
