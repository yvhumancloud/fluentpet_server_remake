"""Interaction writes: press reuse, counters. Every write path ends in `finalize`."""

from datetime import UTC, datetime

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import (
    Button,
    ButtonPress,
    Context,
    Interaction,
    InteractionContext,
    ModeledPusher,
    Pusher,
)
from app.schemas import InteractionOut, PressOut


def invalid(what: str) -> ApiError:
    return ApiError(f"{what} not found", code="validation_error", status=422)


async def _check_ids(session: AsyncSession, stmt, ids: list[int], what: str) -> None:
    if ids and len(set(ids)) != await session.scalar(
        select(func.count()).select_from(stmt.where(stmt.selected_columns[0].in_(ids)).subquery())
    ):
        raise invalid(what)


async def validate_refs(
    session: AsyncSession,
    household_id: int,
    *,
    pusher_id: int | None = None,
    button_ids: list[int] = (),
    context_ids: list[int] = (),
    modeled_pusher_ids: list[int] = (),
) -> None:
    pushers = select(Pusher.id).where(Pusher.household_id == household_id)
    if pusher_id is not None:
        await _check_ids(session, pushers, [pusher_id], "pusher")
    await _check_ids(session, pushers, list(modeled_pusher_ids), "modeled pusher")
    await _check_ids(
        session,
        select(Button.id).where(Button.household_id == household_id, Button.deleted_at.is_(None)),
        list(button_ids),
        "button",
    )
    await _check_ids(
        session,
        select(Context.id).where(
            or_(Context.household_id.is_(None), Context.household_id == household_id)
        ),
        list(context_ids),
        "context",
    )


async def set_presses(
    session: AsyncSession, interaction: Interaction, button_ids: list[int]
) -> None:
    """Replace the press list, reusing rows whose button matches so device timestamps survive."""
    existing = list(
        await session.scalars(
            select(ButtonPress)
            .where(ButtonPress.interaction_id == interaction.id)
            .order_by(ButtonPress.press_order)
        )
    )
    for order, button_id in enumerate(button_ids):
        match = next((p for p in existing if p.button_id == button_id), None)
        if match:
            existing.remove(match)
            match.press_order = order
        else:
            session.add(
                ButtonPress(interaction_id=interaction.id, button_id=button_id, press_order=order)
            )
    for leftover in existing:
        await session.delete(leftover)
    await session.flush()


async def set_contexts(session: AsyncSession, interaction: Interaction, ids: list[int]) -> None:
    await session.execute(
        delete(InteractionContext).where(InteractionContext.interaction_id == interaction.id)
    )
    session.add_all(
        InteractionContext(interaction_id=interaction.id, context_id=c) for c in set(ids)
    )
    await session.flush()


async def set_modeled(session: AsyncSession, interaction: Interaction, ids: list[int]) -> None:
    await session.execute(
        delete(ModeledPusher).where(ModeledPusher.interaction_id == interaction.id)
    )
    session.add_all(ModeledPusher(interaction_id=interaction.id, pusher_id=p) for p in set(ids))
    await session.flush()


async def apply_modeling(session: AsyncSession, interaction: Interaction) -> None:
    """PRD §7 Modeling: human pusher ⇒ Modeled + sole learner; otherwise strip both.

    Contexts whose applies_to conflicts with the pusher kind are removed either way.
    """
    pusher = await session.get(Pusher, interaction.pusher_id) if interaction.pusher_id else None
    kind = "human" if pusher and pusher.is_human else "learner"
    if kind == "human":
        modeled = await session.scalar(
            select(Context.id).where(Context.household_id.is_(None), Context.text == "Modeled")
        )
        learners = list(
            await session.scalars(
                select(Pusher.id).where(
                    Pusher.household_id == interaction.household_id,
                    ~Pusher.is_human,
                    ~Pusher.is_hidden,
                )
            )
        )
        rows = [{"interaction_id": interaction.id, "context_id": modeled}]
        await session.execute(insert(InteractionContext).values(rows).on_conflict_do_nothing())
        if len(learners) == 1:
            rows = [{"interaction_id": interaction.id, "pusher_id": learners[0]}]
            await session.execute(insert(ModeledPusher).values(rows).on_conflict_do_nothing())
    else:
        await session.execute(
            delete(ModeledPusher).where(ModeledPusher.interaction_id == interaction.id)
        )
    conflict = "learner" if kind == "human" else "human"
    await session.execute(
        delete(InteractionContext).where(
            InteractionContext.interaction_id == interaction.id,
            InteractionContext.context_id.in_(
                select(Context.id).where(Context.applies_to == conflict)
            ),
        )
    )


async def finalize(
    session: AsyncSession, ids: list[int], pusher_ids: list[int | None] = ()
) -> None:
    """Recompute num_presses/duration for `ids` and interactions_count for touched pushers."""
    presses = select(ButtonPress).where(ButtonPress.interaction_id == Interaction.id)
    n = presses.with_only_columns(func.count()).scalar_subquery()
    span = func.extract(
        "epoch", func.max(ButtonPress.occurred_at) - func.min(ButtonPress.occurred_at)
    )
    d = presses.with_only_columns(func.coalesce(span, 0)).scalar_subquery()
    await session.execute(
        update(Interaction)
        .where(Interaction.id.in_(ids))
        .values(num_presses=n, duration_seconds=d)
        .execution_options(synchronize_session=False)
    )
    touched = [p for p in pusher_ids if p is not None]
    if touched:
        c = (
            select(func.count())
            .where(Interaction.pusher_id == Pusher.id, Interaction.deleted_at.is_(None))
            .scalar_subquery()
        )
        await session.execute(
            update(Pusher)
            .where(Pusher.id.in_(touched))
            .values(interactions_count=c)
            .execution_options(synchronize_session=False)
        )


async def load(session: AsyncSession, ids: list[int]) -> list[InteractionOut]:
    """Hydrate interactions in the given order (four queries, no N+1)."""
    if not ids:
        return []
    rows = await session.execute(
        select(Interaction, Pusher)
        .outerjoin(Pusher, Pusher.id == Interaction.pusher_id)
        .where(Interaction.id.in_(ids))
        .execution_options(populate_existing=True)
    )
    presses: dict[int, list[PressOut]] = {}
    for press, text in await session.execute(
        select(ButtonPress, Button.text)
        .join(Button, Button.id == ButtonPress.button_id)
        .where(ButtonPress.interaction_id.in_(ids))
        .order_by(ButtonPress.press_order)
    ):
        presses.setdefault(press.interaction_id, []).append(
            PressOut(
                **{c: getattr(press, c) for c in PressOut.model_fields if c != "text"}, text=text
            )
        )
    contexts: dict[int, list[Context]] = {}
    for iid, ctx in await session.execute(
        select(InteractionContext.interaction_id, Context)
        .join(Context, Context.id == InteractionContext.context_id)
        .where(InteractionContext.interaction_id.in_(ids))
        .order_by(Context.text)
    ):
        contexts.setdefault(iid, []).append(ctx)
    modeled: dict[int, list[Pusher]] = {}
    for iid, pusher in await session.execute(
        select(ModeledPusher.interaction_id, Pusher)
        .join(Pusher, Pusher.id == ModeledPusher.pusher_id)
        .where(ModeledPusher.interaction_id.in_(ids))
        .order_by(Pusher.name)
    ):
        modeled.setdefault(iid, []).append(pusher)
    out = {
        i.id: InteractionOut(
            **{c: getattr(i, c) for c in InteractionOut.model_fields if c not in _NESTED},
            pusher=p,
            presses=presses.get(i.id, []),
            contexts=contexts.get(i.id, []),
            modeled_pushers=modeled.get(i.id, []),
        )
        for i, p in rows
    }
    return [out[i] for i in ids if i in out]


_NESTED = {"type", "pusher", "presses", "contexts", "modeled_pushers"}


async def split(session: AsyncSession, interaction: Interaction) -> list[int]:
    """PRD §7 Split: one `split`-origin interaction per timestamped press; original soft-deleted."""
    presses = list(
        await session.scalars(
            select(ButtonPress)
            .where(ButtonPress.interaction_id == interaction.id)
            .order_by(ButtonPress.press_order)
        )
    )
    if len(presses) < 2:
        raise ApiError("nothing to split", code="validation_error", status=422)
    if any(p.occurred_at is None for p in presses):
        raise ApiError(
            "presses entered by hand have no timestamps", code="validation_error", status=422
        )
    context_ids = list(
        await session.scalars(
            select(InteractionContext.context_id).where(
                InteractionContext.interaction_id == interaction.id
            )
        )
    )
    modeled_ids = list(
        await session.scalars(
            select(ModeledPusher.pusher_id).where(ModeledPusher.interaction_id == interaction.id)
        )
    )
    parts = []
    for press in presses:
        part = Interaction(
            **{c: getattr(interaction, c) for c in COPIED},
            origin="split",
            occurred_at=press.occurred_at,
        )
        session.add(part)
        await session.flush()
        press.interaction_id, press.press_order = part.id, 0
        await set_contexts(session, part, context_ids)
        await set_modeled(session, part, modeled_ids)
        parts.append(part.id)
    interaction.deleted_at = func.now()
    await session.flush()
    await finalize(session, parts, [interaction.pusher_id])
    return parts


COPIED = (
    "household_id",
    "pusher_id",
    "note",
    "device_timezone",
    "is_favourite",
    "is_hidden",
    "created_by_user_id",
    "created_by_base_id",
)


async def merge(session: AsyncSession, target: Interaction, sources: list[Interaction]) -> None:
    """PRD §7 Merge: target absorbs presses, contexts, modeled pushers; sources soft-deleted."""
    every = [target, *sources]
    ids = [i.id for i in every]
    started = {i.id: i.occurred_at for i in every}
    presses = list(
        await session.scalars(select(ButtonPress).where(ButtonPress.interaction_id.in_(ids)))
    )
    presses.sort(key=lambda p: (started[p.interaction_id], p.press_order, p.occurred_at or MAX))
    for order, press in enumerate(presses):
        press.interaction_id, press.press_order = target.id, order
    for link, col in ((InteractionContext, "context_id"), (ModeledPusher, "pusher_id")):
        keys = set(
            await session.scalars(select(getattr(link, col)).where(link.interaction_id.in_(ids)))
        )
        await session.execute(delete(link).where(link.interaction_id.in_(ids)))
        session.add_all(link(interaction_id=target.id, **{col: k}) for k in keys)
    target.occurred_at = min(started.values())
    for s in sources:
        s.deleted_at = func.now()
    await session.flush()
    await apply_modeling(session, target)
    await finalize(session, [target.id], [i.pusher_id for i in every])


MAX = datetime.max.replace(tzinfo=UTC)
