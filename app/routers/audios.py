import zlib
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from sqlalchemy import select, update

from app.auth import CurrentUser, DbSession
from app.errors import NotFound
from app.models import Audio, BaseButton, Button, User
from app.schemas import AudioOut, UrlOut
from app.services import storage

router = APIRouter(prefix="/audios", tags=["audios"])


async def get_audio(session: DbSession, user: User, audio_id: int) -> Audio:
    audio = await session.scalar(
        select(Audio).where(Audio.id == audio_id, Audio.household_id == user.household_id)
    )
    if audio is None:
        raise NotFound("audio not found")
    return audio


@router.get("")
async def list_audios(user: CurrentUser, session: DbSession) -> list[AudioOut]:
    return list(
        await session.scalars(
            select(Audio).where(Audio.household_id == user.household_id).order_by(Audio.id)
        )
    )


@router.post("", status_code=201)
async def upload_audio(
    name: Annotated[str, Form(min_length=1, max_length=100)],
    file: Annotated[UploadFile, File()],
    user: CurrentUser,
    session: DbSession,
) -> AudioOut:
    data = await file.read(storage.AUDIO_MAX_BYTES + 1)
    storage.check_opus(data)
    audio = Audio(
        household_id=user.household_id,
        name=name,
        storage_key="",
        crc32=zlib.crc32(data),
        byte_size=len(data),
    )
    session.add(audio)
    await session.flush()
    audio.storage_key = f"audio/h{audio.household_id}/{audio.id}.ogg"
    storage.put(audio.storage_key, data, "audio/ogg")
    return audio


@router.get("/{audio_id}/url")
async def audio_url(audio_id: int, user: CurrentUser, session: DbSession) -> UrlOut:
    audio = await get_audio(session, user, audio_id)
    return UrlOut(url=storage.presigned_get(audio.storage_key))


@router.delete("/{audio_id}", status_code=204)
async def delete_audio(audio_id: int, user: CurrentUser, session: DbSession) -> None:
    audio = await get_audio(session, user, audio_id)
    # linked buttons that played this clip need the base told to drop it
    await session.execute(
        update(BaseButton)
        .where(BaseButton.desired_audio_id == audio.id)
        .values(desired_audio_id=None, desired_version=BaseButton.desired_version + 1)
    )
    await session.execute(update(Button).where(Button.audio_id == audio.id).values(audio_id=None))
    await session.delete(audio)
    await session.flush()
    storage.delete(audio.storage_key)
