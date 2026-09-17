from typing import Annotated

from fastapi import APIRouter, File, Response, UploadFile
from sqlalchemy import cast, delete, func, select, update
from sqlalchemy.dialects.postgresql import JSONB

from app.auth import CurrentUser, DbSession
from app.errors import Conflict, NotFound
from app.models import BaseStation, LearnerType, Preference, Pusher, User
from app.schemas import LearnerTypeOut, PusherDetailOut, PusherIn, PusherPatch
from app.services import storage

router = APIRouter(tags=["pushers"])


@router.get("/learner-types")
async def learner_types(user: CurrentUser, session: DbSession) -> list[LearnerTypeOut]:
    return list(await session.scalars(select(LearnerType).order_by(LearnerType.id)))


async def get_pusher(session: DbSession, user: User, pusher_id: int) -> Pusher:
    pusher = await session.scalar(
        select(Pusher).where(Pusher.id == pusher_id, Pusher.household_id == user.household_id)
    )
    if pusher is None:
        raise NotFound("pusher not found")
    return pusher


@router.get("/pushers")
async def list_pushers(
    user: CurrentUser, session: DbSession, include_hidden: bool = False
) -> list[PusherDetailOut]:
    q = select(Pusher).where(Pusher.household_id == user.household_id)
    if not include_hidden:
        q = q.where(~Pusher.is_hidden)
    return list(await session.scalars(q.order_by(Pusher.interactions_count.desc(), Pusher.id)))


@router.post("/pushers", status_code=201)
async def create_pusher(
    body: PusherIn, user: CurrentUser, session: DbSession, response: Response
) -> PusherDetailOut:
    existing = await session.scalar(
        select(Pusher).where(
            Pusher.household_id == user.household_id,
            func.lower(Pusher.name) == body.name.lower(),
        )
    )
    if existing and not existing.is_hidden:
        raise Conflict("a pusher with that name already exists")
    if existing:
        existing.is_hidden = False
        response.status_code = 200
        return existing
    pusher = Pusher(household_id=user.household_id, **body.model_dump())
    session.add(pusher)
    await session.flush()
    return pusher


@router.get("/pushers/{pusher_id}")
async def show_pusher(pusher_id: int, user: CurrentUser, session: DbSession) -> PusherDetailOut:
    return await get_pusher(session, user, pusher_id)


@router.patch("/pushers/{pusher_id}")
async def patch_pusher(
    pusher_id: int, body: PusherPatch, user: CurrentUser, session: DbSession
) -> PusherDetailOut:
    pusher = await get_pusher(session, user, pusher_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(pusher, k, v)
    if body.is_hidden:
        await hide_pusher(session, pusher)
    await session.flush()
    return pusher


async def hide_pusher(session: DbSession, pusher: Pusher) -> None:
    """A hidden pusher can't be anyone's default."""
    await session.execute(
        update(BaseStation)
        .where(BaseStation.default_pusher_id == pusher.id)
        .values(default_pusher_id=None)
    )
    await session.execute(
        delete(Preference).where(
            Preference.key == "default_pusher_id",
            Preference.value == cast(pusher.id, JSONB),
            Preference.user_id.in_(select(User.id).where(User.household_id == pusher.household_id)),
        )
    )


@router.delete("/pushers/{pusher_id}", status_code=204)
async def delete_pusher(pusher_id: int, user: CurrentUser, session: DbSession) -> None:
    pusher = await get_pusher(session, user, pusher_id)
    if pusher.avatar_key:
        storage.delete(pusher.avatar_key)
    await session.delete(pusher)


AVATAR_MAX_BYTES = 2 * 1024 * 1024


@router.put("/pushers/{pusher_id}/avatar")
async def put_avatar(
    pusher_id: int, file: Annotated[UploadFile, File()], user: CurrentUser, session: DbSession
) -> PusherDetailOut:
    pusher = await get_pusher(session, user, pusher_id)
    data = await file.read(AVATAR_MAX_BYTES + 1)
    ext = storage.check_image(data, AVATAR_MAX_BYTES)
    key = f"avatars/h{pusher.household_id}/p{pusher.id}.{ext}"
    storage.put(key, data, file.content_type or f"image/{ext}")
    pusher.avatar_key = key
    await session.flush()
    return pusher
