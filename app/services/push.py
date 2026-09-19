"""FCM push to every member of a household. `fcm_send` is the boundary tests replace."""

import asyncio
import logging
from datetime import timedelta
from itertools import batched

from firebase_admin import messaging
from sqlalchemy import delete, func, select

from app.auth import firebase_app
from app.db import SessionLocal
from app.models import PushLog, PushToken, User

log = logging.getLogger("fluentpet.push")
DEAD_TOKEN_ERRORS = {"UnregisteredError", "SenderIdMismatchError"}
CHUNK = 500

TITLES = {
    "base_registered": ("Base registered", "{name} is now part of your household."),
    "base_removed": ("Base removed", "{name} is no longer part of your household."),
    "button_linked": ("Button linked", "{text} was linked to {base}."),
    "button_unlinked": ("Button unlinked", "{text} was unlinked from {base}."),
    "button_pressed": ("Button pressed", "{text} pressed on {base}"),
    "base_battery_low": ("Base battery low", "{base} is at {level}%"),
    "base_fully_charged": ("Base fully charged", "{base} is fully charged"),
    "base_offline": ("Base offline", "{base} has been offline for over a day"),
    "weekly_digest": ("Weekly digest", "{text}"),
}
RATE_LIMITED = {"base_battery_low", "base_fully_charged"}  # 1 per user per 24 h


def fcm_send(tokens: list[str], title: str, body: str, data: dict[str, str]) -> list[str | None]:
    """One multicast. Returns, per token, the FCM error class name or None on success."""
    resp = messaging.send_each_for_multicast(
        messaging.MulticastMessage(
            tokens=tokens, notification=messaging.Notification(title, body), data=data
        ),
        app=firebase_app(),
    )
    return [None if r.success else type(r.exception).__name__ for r in resp.responses]


async def notify_household(
    household_id: int,
    key: str,
    *,
    base_id: int | None = None,
    user_ids: list[int] | None = None,
    **fmt: str,
) -> None:
    """Runs after commit, so it opens its own session. `user_ids` narrows the recipients."""
    title, body = TITLES[key]
    body = body.format(**fmt)
    data = {"key": key, **{k: str(v) for k, v in fmt.items()}}
    async with SessionLocal() as session, session.begin():
        q = (
            select(PushToken.token, PushToken.user_id)
            .join(User, User.id == PushToken.user_id)
            .where(User.household_id == household_id)
        )
        if user_ids is not None:
            q = q.where(User.id.in_(user_ids))
        if key in RATE_LIMITED:
            recent = select(PushLog.user_id).where(
                PushLog.key == key, PushLog.sent_at > func.now() - timedelta(hours=24)
            )
            q = q.where(User.id.not_in(recent))
        rows = (await session.execute(q)).all()
        for chunk in batched(rows, CHUNK, strict=False):
            tokens = [t for t, _ in chunk]
            try:
                errors = await asyncio.to_thread(fcm_send, tokens, title, body, data)
            except Exception as e:  # network / auth failure: log it, never crash the request
                log.exception("fcm multicast failed")
                errors = [type(e).__name__] * len(tokens)
            dead = []
            for (token, user_id), err in zip(chunk, errors, strict=True):
                session.add(
                    PushLog(
                        user_id=user_id,
                        household_id=household_id,
                        base_id=base_id,
                        key=key,
                        error=err,
                    )
                )
                if err in DEAD_TOKEN_ERRORS:
                    dead.append(token)
            if dead:
                await session.execute(delete(PushToken).where(PushToken.token.in_(dead)))
