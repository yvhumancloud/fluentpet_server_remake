from fastapi import APIRouter, Response
from sqlalchemy import select

from app.auth import CurrentUser, DbSession
from app.db import AfterCommit
from app.errors import ApiError, NotFound
from app.models import BaseButton, BaseStation, Button, Pusher, User
from app.schemas import BaseButtonOut, BaseCreate, BaseOut, BasePatch, LinkedButtonOut
from app.services import push

router = APIRouter(prefix="/bases", tags=["bases"])


async def base_out(session: DbSession, base: BaseStation) -> BaseOut:
    rows = await session.execute(
        select(BaseButton, Button.text)
        .join(Button, Button.id == BaseButton.button_id)
        .where(BaseButton.base_id == base.id)
        .order_by(BaseButton.id)
    )
    buttons = [
        LinkedButtonOut(
            **{c: getattr(bb, c) for c in BaseButtonOut.model_fields},
            button_id=bb.button_id,
            text=t,
        )
        for bb, t in rows
    ]
    return BaseOut(
        **{c: getattr(base, c) for c in BaseOut.model_fields if c != "buttons"}, buttons=buttons
    )


async def get_base(session: DbSession, user: User, serial: str) -> BaseStation:
    base = await session.scalar(
        select(BaseStation).where(
            BaseStation.serial_number == serial.upper(),
            BaseStation.household_id == user.household_id,
        )
    )
    if base is None:
        raise NotFound("base not found")
    return base


@router.get("")
async def list_bases(user: CurrentUser, session: DbSession) -> list[BaseOut]:
    bases = await session.scalars(
        select(BaseStation)
        .where(BaseStation.household_id == user.household_id)
        .order_by(BaseStation.id)
    )
    return [await base_out(session, b) for b in bases]


@router.post("", status_code=201)
async def register_base(
    body: BaseCreate,
    user: CurrentUser,
    session: DbSession,
    response: Response,
    after_commit: AfterCommit,
) -> BaseOut:
    serial = body.serial_number.upper()
    existing = await session.scalar(select(BaseStation).where(BaseStation.serial_number == serial))
    if existing and existing.household_id == user.household_id:
        response.status_code = 200
        return await base_out(session, existing)
    if existing:  # transfer: the other household loses it, links and all
        await session.delete(existing)
        await session.flush()
        after_commit.add_task(
            push.notify_household,
            existing.household_id,
            "base_removed",
            name=existing.name or serial,
        )
    base = BaseStation(
        household_id=user.household_id,
        serial_number=serial,
        name=body.name,
        created_by_user_id=user.id,
    )
    session.add(base)
    await session.flush()
    after_commit.add_task(
        push.notify_household, user.household_id, "base_registered", base_id=base.id, name=base.name
    )
    return await base_out(session, base)


@router.patch("/{serial}")
async def patch_base(
    serial: str, body: BasePatch, user: CurrentUser, session: DbSession
) -> BaseOut:
    base = await get_base(session, user, serial)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("default_pusher_id") is not None:
        ok = await session.scalar(
            select(Pusher.id).where(
                Pusher.id == changes["default_pusher_id"],
                Pusher.household_id == user.household_id,
                ~Pusher.is_hidden,
            )
        )
        if not ok:
            raise ApiError(
                "default_pusher_id must be a visible pusher", code="validation_error", status=422
            )
    for k, v in changes.items():
        setattr(base, k, v)
    await session.flush()
    return await base_out(session, base)


@router.delete("/{serial}", status_code=204)
async def delete_base(
    serial: str, user: CurrentUser, session: DbSession, after_commit: AfterCommit
) -> None:
    base = await get_base(session, user, serial)
    await session.delete(base)
    after_commit.add_task(
        push.notify_household,
        user.household_id,
        "base_removed",
        name=base.name or base.serial_number,
    )
