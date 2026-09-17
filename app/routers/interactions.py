from fastapi import APIRouter
from sqlalchemy import func, select

from app.auth import CurrentUser, DbSession
from app.errors import NotFound
from app.models import Interaction, User
from app.schemas import (
    BulkIn,
    BulkOut,
    InteractionIn,
    InteractionMerge,
    InteractionOut,
    InteractionPatch,
)
from app.services import interactions as svc

router = APIRouter(tags=["interactions"])
LISTS = ("button_ids", "context_ids", "modeled_pusher_ids")


async def get_interaction(session: DbSession, user: User, interaction_id: int) -> Interaction:
    interaction = await session.scalar(
        select(Interaction).where(
            Interaction.id == interaction_id,
            Interaction.household_id == user.household_id,
            Interaction.deleted_at.is_(None),
        )
    )
    if interaction is None:
        raise NotFound("interaction not found")
    return interaction


async def one(session: DbSession, interaction_id: int) -> InteractionOut:
    return (await svc.load(session, [interaction_id]))[0]


@router.post("/interactions", status_code=201)
async def create_interaction(
    body: InteractionIn, user: CurrentUser, session: DbSession
) -> InteractionOut:
    await svc.validate_refs(
        session,
        user.household_id,
        pusher_id=body.pusher_id,
        button_ids=body.button_ids,
        context_ids=body.context_ids,
        modeled_pusher_ids=body.modeled_pusher_ids,
    )
    interaction = Interaction(
        household_id=user.household_id,
        created_by_user_id=user.id,
        **body.model_dump(exclude=set(LISTS)),
    )
    session.add(interaction)
    await session.flush()
    await svc.set_presses(session, interaction, body.button_ids)
    await svc.set_contexts(session, interaction, body.context_ids)
    await svc.set_modeled(session, interaction, body.modeled_pusher_ids)
    await svc.apply_modeling(session, interaction)
    await svc.finalize(session, [interaction.id], [interaction.pusher_id])
    return await one(session, interaction.id)


@router.get("/interactions/{interaction_id}")
async def show_interaction(
    interaction_id: int, user: CurrentUser, session: DbSession
) -> InteractionOut:
    await get_interaction(session, user, interaction_id)
    return await one(session, interaction_id)


@router.patch("/interactions/{interaction_id}")
async def patch_interaction(
    interaction_id: int, body: InteractionPatch, user: CurrentUser, session: DbSession
) -> InteractionOut:
    interaction = await get_interaction(session, user, interaction_id)
    changes = body.model_dump(exclude_unset=True)
    lists = {k: changes.pop(k) for k in LISTS if k in changes}
    await svc.validate_refs(session, user.household_id, pusher_id=changes.get("pusher_id"), **lists)
    old_pusher = interaction.pusher_id
    for k, v in changes.items():
        setattr(interaction, k, v)
    await session.flush()
    for k, setter in zip(LISTS, (svc.set_presses, svc.set_contexts, svc.set_modeled), strict=True):
        if k in lists:
            await setter(session, interaction, lists[k])
    await svc.apply_modeling(session, interaction)
    await svc.finalize(session, [interaction.id], [old_pusher, interaction.pusher_id])
    return await one(session, interaction.id)


@router.delete("/interactions/{interaction_id}", status_code=204)
async def delete_interaction(interaction_id: int, user: CurrentUser, session: DbSession) -> None:
    interaction = await get_interaction(session, user, interaction_id)
    interaction.deleted_at = func.now()
    await session.flush()
    await svc.finalize(session, [], [interaction.pusher_id])


@router.post("/interactions/{interaction_id}/split")
async def split_interaction(
    interaction_id: int, user: CurrentUser, session: DbSession
) -> list[InteractionOut]:
    interaction = await get_interaction(session, user, interaction_id)
    return await svc.load(session, await svc.split(session, interaction))


@router.post("/interactions/{interaction_id}/merge")
async def merge_interactions(
    interaction_id: int, body: InteractionMerge, user: CurrentUser, session: DbSession
) -> InteractionOut:
    target = await get_interaction(session, user, interaction_id)
    wanted = set(body.interaction_ids) - {target.id}
    sources = list(
        await session.scalars(
            select(Interaction).where(
                Interaction.id.in_(wanted),
                Interaction.household_id == user.household_id,
                Interaction.deleted_at.is_(None),
            )
        )
    )
    if len(sources) != len(wanted):
        raise NotFound("interaction not found")
    await svc.merge(session, target, sources)
    return await one(session, target.id)


@router.post("/interactions/bulk")
async def bulk(body: BulkIn, user: CurrentUser, session: DbSession) -> BulkOut:
    q = select(Interaction).where(
        Interaction.household_id == user.household_id, Interaction.deleted_at.is_(None)
    )
    if body.ids:
        q = q.where(Interaction.id.in_(body.ids))
    rows = list(await session.scalars(q.order_by(Interaction.occurred_at, Interaction.id)))
    if body.ids and len(rows) != len(set(body.ids)):
        raise NotFound("interaction not found")
    target = None
    match body.operation:
        case "assign":
            await svc.validate_refs(session, user.household_id, pusher_id=body.pusher_id)
            was = [i.pusher_id for i in rows]
            for i in rows:
                i.pusher_id = body.pusher_id
            await session.flush()
            for i in rows:
                await svc.apply_modeling(session, i)
            await svc.finalize(session, [i.id for i in rows], [*was, body.pusher_id])
        case "delete":
            for i in rows:
                i.deleted_at = func.now()
            await session.flush()
            await svc.finalize(session, [], [i.pusher_id for i in rows])
        case "merge" if rows:
            by_id = {i.id: i for i in rows}
            target = by_id[body.ids[0]] if body.ids else rows[0]
            await svc.merge(session, target, [i for i in rows if i is not target])
    return BulkOut(affected=len(rows), id=target.id if target else None)
