"""PRD §7 press grouping for base `press` events."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BaseButton, BaseStation, ButtonPress, Interaction, Preference, User
from app.services import interactions as svc


async def record_press(
    session: AsyncSession, base: BaseStation, link: BaseButton, occurred_at: datetime
) -> tuple[Interaction, bool] | None:
    """Attach the press to the base's open interaction or start one.

    Returns (interaction, started_new), or None when the press duplicates one already stored
    (same button, same second).
    """
    reported = int(occurred_at.timestamp())
    occurred_at = min(occurred_at, datetime.now(UTC))
    window = timedelta(seconds=base.group_window_seconds)
    interaction = await session.scalar(
        select(Interaction)
        .join(ButtonPress, ButtonPress.interaction_id == Interaction.id)
        .where(
            Interaction.created_by_base_id == base.id,
            Interaction.origin == "base",
            Interaction.deleted_at.is_(None),
            ButtonPress.occurred_at.between(occurred_at - window, occurred_at + window),
        )
        .order_by(ButtonPress.occurred_at.desc())
        .limit(1)
    )
    started = interaction is None
    if started:
        interaction = Interaction(
            household_id=base.household_id,
            origin="base",
            created_by_base_id=base.id,
            occurred_at=occurred_at,
            pusher_id=base.default_pusher_id or await _default_pusher(session, base),
            device_timezone=await _timezone(session, base),
        )
        session.add(interaction)
        await session.flush()
    elif await session.scalar(
        select(ButtonPress.id).where(
            ButtonPress.interaction_id == interaction.id,
            ButtonPress.button_id == link.button_id,
            func.date_trunc("second", ButtonPress.occurred_at)
            == func.date_trunc("second", occurred_at),
        )
    ):
        return None
    order = await session.scalar(
        select(func.count()).where(
            ButtonPress.interaction_id == interaction.id, ButtonPress.occurred_at <= occurred_at
        )
    )
    await session.execute(
        update(ButtonPress)
        .where(ButtonPress.interaction_id == interaction.id, ButtonPress.press_order >= order)
        .values(press_order=ButtonPress.press_order + 1)
        .execution_options(synchronize_session=False)
    )
    session.add(
        ButtonPress(
            interaction_id=interaction.id,
            button_id=link.button_id,
            press_order=order,
            occurred_at=occurred_at,
            reported_timestamp=reported,
        )
    )
    if order == 0:
        interaction.occurred_at = occurred_at
    await session.flush()
    await svc.apply_modeling(session, interaction)
    await svc.finalize(session, [interaction.id], [interaction.pusher_id])
    return interaction, started


def _owner_first(base: BaseStation):
    """Household members, the base's creator first."""
    return (
        select(User)
        .where(User.household_id == base.household_id)
        .order_by((User.id == base.created_by_user_id).desc(), User.id)
    )


async def _default_pusher(session: AsyncSession, base: BaseStation) -> int | None:
    value = await session.scalar(
        select(Preference.value)
        .join(User, User.id == Preference.user_id)
        .where(User.household_id == base.household_id, Preference.key == "default_pusher_id")
        .order_by((User.id == base.created_by_user_id).desc(), User.id)
        .limit(1)
    )
    return value if isinstance(value, int) else None


async def _timezone(session: AsyncSession, base: BaseStation) -> str:
    return (
        await session.scalar(_owner_first(base).with_only_columns(User.timezone).limit(1)) or "UTC"
    )


async def push_recipients(session: AsyncSession, household_id: int, started: bool) -> list[int]:
    """Members whose push_frequency wants this press: all (default) or on_interaction (first)."""
    rows = await session.execute(
        select(User.id, Preference.value)
        .outerjoin(
            Preference,
            (Preference.user_id == User.id) & (Preference.key == "push_frequency"),
        )
        .where(User.household_id == household_id)
    )
    return [
        uid for uid, freq in rows if freq in (None, "all") or (freq == "on_interaction" and started)
    ]
