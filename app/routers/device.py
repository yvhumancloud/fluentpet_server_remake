from functools import partial

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.auth import DbSession, require_device_key
from app.db import AfterCommit
from app.errors import Forbidden, NotFound
from app.models import Audio, BaseButton, BaseStation, Button, DeviceEvent
from app.schemas import (
    AckIn,
    AudioUrlIn,
    AudioUrlOut,
    DesiredAudio,
    DesiredOut,
    DeviceEventIn,
    DeviceEventResult,
    DeviceStateIn,
)
from app.services import devices, presses, push, storage, webhooks

router = APIRouter(
    prefix="/device",
    tags=["device"],
    dependencies=[Depends(require_device_key)],
    include_in_schema=False,
)


@router.post("/events")
async def post_events(
    events: list[DeviceEventIn], session: DbSession, after_commit: AfterCommit
) -> list[DeviceEventResult]:
    results = []
    for ev in events:
        results.append(DeviceEventResult(status=await _handle(session, after_commit, ev)))
    return results


async def _handle(session: DbSession, after_commit: AfterCommit, ev: DeviceEventIn) -> str:
    base = await session.scalar(
        select(BaseStation).where(BaseStation.serial_number == ev.serial_number.upper())
    )
    if base is None:
        return "unknown_base"
    serial = devices.clean_button_serial(ev.button_serial_number)
    if ev.button_serial_number is not None and serial is None:
        return "invalid"
    payload = ev.payload or {}
    level = payload.get("level")
    if ev.type == "battery" and not (isinstance(level, int) and 0 <= level <= 100):
        return "invalid"
    inserted = await session.execute(
        insert(DeviceEvent)
        .values(
            base_id=base.id,
            button_serial_number=serial,
            event_type=ev.type,
            payload=ev.payload,
            occurred_at=ev.occurred_at,
        )
        .on_conflict_do_nothing()
    )
    if inserted.rowcount == 0:
        return "duplicate"
    base.last_online_at = max(base.last_online_at or ev.occurred_at, ev.occurred_at)
    notify = partial(
        after_commit.add_task,
        push.notify_household,
        base.household_id,
        base_id=base.id,
        base=base.name or base.serial_number,
    )
    if ev.type == "battery" and not serial:
        base.battery_level, base.battery_updated_at = level, ev.occurred_at
        charging = payload.get("charging", (base.reported_state or {}).get("charging", False))
        if level < 20 and not charging:
            notify("base_battery_low", level=str(level))
    elif ev.type == "power":
        base.reported_state = {
            **(base.reported_state or {}),
            "charging": bool(payload.get("charging")),
        }
    elif ev.type == "fully_charged":
        notify("base_fully_charged")
    if ev.type in ("button_seen", "press", "battery") and serial:
        link, created = await devices.ensure_linked(session, base, serial)
        link.last_online_at = ev.occurred_at
        recorded = None
        if ev.type == "press":
            recorded = await presses.record_press(session, base, link, ev.occurred_at)
            if recorded is None:
                return "duplicate"
        elif ev.type == "battery":
            link.battery_level, link.battery_updated_at = level, ev.occurred_at
        button = await session.get_one(Button, link.button_id)
        notify = partial(notify, text=button.text)
        if created:
            notify("button_linked")
        if recorded:
            _, started = recorded
            recipients = await presses.push_recipients(session, base.household_id, started)
            if recipients:
                notify("button_pressed", user_ids=recipients)
            if button.webhook_url:
                after_commit.add_task(webhooks.call, button.id, button.webhook_url)
    return "created"


async def get_base(session: DbSession, serial: str) -> BaseStation:
    base = await session.scalar(
        select(BaseStation).where(BaseStation.serial_number == serial.upper())
    )
    if base is None:
        raise NotFound("base not found")
    return base


@router.put("/bases/{serial}/state", status_code=204)
async def report_state(serial: str, body: DeviceStateIn, session: DbSession) -> None:
    base = await get_base(session, serial)
    base.reported_state = body.reported_state
    base.last_online_at = func.now()
    if body.fw_version is not None:
        base.fw_version = body.fw_version
    if body.battery_level is not None:
        base.battery_level, base.battery_updated_at = body.battery_level, func.now()


@router.get("/desired")
async def desired(session: DbSession, serial_number: str) -> list[DesiredOut]:
    """Links the base has not applied yet, with a signed audio URL when one is wanted."""
    q = (
        select(BaseButton, BaseStation.serial_number, Audio)
        .join(BaseStation, BaseStation.id == BaseButton.base_id)
        .outerjoin(Audio, Audio.id == BaseButton.desired_audio_id)
        .where(
            BaseStation.serial_number == serial_number.upper(),
            BaseButton.desired_version > BaseButton.applied_version,
        )
        .order_by(BaseButton.button_serial_number)
    )
    return [
        DesiredOut(
            base_button_id=link.id,
            serial_number=serial,
            button_serial_number=link.button_serial_number,
            desired_deleted=link.desired_deleted,
            desired_version=link.desired_version,
            applied_version=link.applied_version,
            audio=DesiredAudio(
                id=audio.id, url=storage.presigned_get(audio.storage_key), crc32=audio.crc32
            )
            if audio
            else None,
        )
        for link, serial, audio in await session.execute(q)
    ]


@router.post("/desired/ack", status_code=204)
async def ack(body: list[AckIn], session: DbSession) -> None:
    """Record what the base applied. A pending unlink is dropped once it is applied."""
    for item in body:
        link = await session.get(BaseButton, item.base_button_id)
        if link is None or item.applied_version <= link.applied_version:
            continue
        link.applied_version = item.applied_version
        if link.desired_deleted and link.applied_version >= link.desired_version:
            await session.delete(link)


@router.post("/audio-url")
async def audio_url(body: AudioUrlIn, session: DbSession) -> AudioUrlOut:
    base = await get_base(session, body.serial_number)
    audio = await session.get(Audio, body.audio_id)
    if audio is None or audio.household_id != base.household_id:
        raise Forbidden("audio does not belong to this base's household")
    return AudioUrlOut(url=storage.presigned_get(audio.storage_key), crc32=audio.crc32)
