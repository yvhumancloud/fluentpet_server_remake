from typing import Literal

from fastapi import APIRouter, Response
from sqlalchemy import func, select, update

from app.auth import CurrentUser, DbSession
from app.db import AfterCommit
from app.errors import ApiError, Conflict, NotFound
from app.models import (
    Audio,
    BaseButton,
    Button,
    ButtonConcept,
    ButtonPress,
    Interaction,
    User,
    WebhookLog,
)
from app.schemas import (
    ButtonConceptOut,
    ButtonCreate,
    ButtonMerge,
    ButtonOut,
    ButtonPatch,
    WebhookLogOut,
)
from app.services import devices
from app.services.buttons import button_words

router = APIRouter(tags=["buttons"])

_press_count = (
    select(func.count(ButtonPress.id))
    .join(Interaction, Interaction.id == ButtonPress.interaction_id)
    .where(ButtonPress.button_id == Button.id, Interaction.deleted_at.is_(None))
    .correlate(Button)
    .scalar_subquery()
    .label("press_count")
)


def _query(user: User):
    return (
        select(Button, BaseButton, _press_count)
        .outerjoin(BaseButton, BaseButton.button_id == Button.id)
        .where(Button.household_id == user.household_id, Button.deleted_at.is_(None))
    )


def _out(row) -> ButtonOut:
    button, base_button, press_count = row
    return ButtonOut(
        **{
            c: getattr(button, c)
            for c in ButtonOut.model_fields
            if c not in ("base_button", "press_count")
        },
        base_button=base_button,
        press_count=press_count,
    )


async def button_out(session: DbSession, user: User, button_id: int) -> ButtonOut:
    row = (await session.execute(_query(user).where(Button.id == button_id))).first()
    if row is None:
        raise NotFound("button not found")
    return _out(row)


async def get_button(session: DbSession, user: User, button_id: int) -> Button:
    button = await session.scalar(
        select(Button).where(
            Button.id == button_id,
            Button.household_id == user.household_id,
            Button.deleted_at.is_(None),
        )
    )
    if button is None:
        raise NotFound("button not found")
    return button


@router.get("/button-concepts")
async def button_concepts(user: CurrentUser, session: DbSession) -> list[ButtonConceptOut]:
    return list(await session.scalars(select(ButtonConcept).order_by(ButtonConcept.id)))


@router.get("/buttons")
async def list_buttons(
    user: CurrentUser,
    session: DbSession,
    sort: Literal["alphabet", "frequency", "date"] = "alphabet",
    include_hidden: bool = False,
) -> list[ButtonOut]:
    q = _query(user)
    if not include_hidden:
        q = q.where(~Button.is_hidden)
    order = {
        "alphabet": (func.lower(Button.text), Button.id),
        "frequency": (_press_count.desc(), func.lower(Button.text)),
        "date": (Button.created_at.desc(), Button.id.desc()),
    }[sort]
    return [_out(r) for r in await session.execute(q.order_by(*order))]


@router.post("/buttons", status_code=201)
async def create_button(
    body: ButtonCreate, user: CurrentUser, session: DbSession, response: Response
) -> ButtonOut:
    text, word, normalized = button_words(body.text)
    existing = await session.scalar(
        select(Button).where(
            Button.household_id == user.household_id,
            Button.deleted_at.is_(None),
            func.lower(Button.text) == text.lower(),
        )
    )
    if existing and not existing.is_hidden:
        raise Conflict("a button with that text already exists")
    if existing:
        existing.is_hidden = False
        response.status_code = 200
        return await button_out(session, user, existing.id)
    button = Button(
        household_id=user.household_id,
        text=text,
        word=word,
        normalized_word=normalized,
        **body.model_dump(exclude={"text"}),
    )
    session.add(button)
    await session.flush()
    return await button_out(session, user, button.id)


@router.get("/buttons/{button_id}")
async def show_button(button_id: int, user: CurrentUser, session: DbSession) -> ButtonOut:
    return await button_out(session, user, button_id)


async def get_link(session: DbSession, button_id: int) -> BaseButton | None:
    return await session.scalar(select(BaseButton).where(BaseButton.button_id == button_id))


@router.patch("/buttons/{button_id}")
async def patch_button(
    button_id: int,
    body: ButtonPatch,
    user: CurrentUser,
    session: DbSession,
    after_commit: AfterCommit,
) -> ButtonOut:
    button = await get_button(session, user, button_id)
    changes = body.model_dump(exclude_unset=True)
    link = await get_link(session, button.id)
    if "text" in changes:
        button.text, button.word, button.normalized_word = button_words(changes.pop("text"))
    if changes.get("audio_id") is not None:
        owned = await session.scalar(
            select(Audio.id).where(
                Audio.id == changes["audio_id"], Audio.household_id == user.household_id
            )
        )
        if not owned:
            raise ApiError("audio not found", code="validation_error", status=422)
    for k, v in changes.items():
        setattr(button, k, v)
    if link and "audio_id" in changes and changes["audio_id"] != link.desired_audio_id:
        link.desired_audio_id = changes["audio_id"]
        link.desired_version += 1
    if link and changes.get("is_hidden"):
        await devices.unlink(session, link, button, after_commit)
    await session.flush()
    return await button_out(session, user, button_id)


@router.delete("/buttons/{button_id}", status_code=204)
async def delete_button(
    button_id: int, user: CurrentUser, session: DbSession, after_commit: AfterCommit
) -> None:
    button = await get_button(session, user, button_id)
    if link := await get_link(session, button.id):
        await devices.unlink(session, link, button, after_commit)
    button.deleted_at = func.now()


@router.post("/buttons/{button_id}/unlink")
async def unlink_button(
    button_id: int, user: CurrentUser, session: DbSession, after_commit: AfterCommit
) -> ButtonOut:
    button = await get_button(session, user, button_id)
    link = await get_link(session, button.id)
    if link is None or link.desired_deleted:
        raise Conflict("button is not linked to a base")
    await devices.unlink(session, link, button, after_commit)
    await session.flush()
    return await button_out(session, user, button_id)


@router.post("/buttons/merge")
async def merge_buttons(body: ButtonMerge, user: CurrentUser, session: DbSession) -> ButtonOut:
    """Target absorbs source's presses, base link, and audio; source is soft-deleted."""
    source = await get_button(session, user, body.source_id)
    target = await get_button(session, user, body.target_id)
    source_link, target_link = (
        await get_link(session, source.id),
        await get_link(session, target.id),
    )
    if source_link and target_link:
        raise Conflict("both buttons are linked to a base; unlink one first")
    await session.execute(
        update(ButtonPress).where(ButtonPress.button_id == source.id).values(button_id=target.id)
    )
    if target.audio_id is None and source.audio_id is not None:
        target.audio_id = source.audio_id
    if source_link:
        source_link.button_id = target.id
        if source_link.desired_audio_id != target.audio_id:
            source_link.desired_audio_id = target.audio_id
            source_link.desired_version += 1
    source.deleted_at = func.now()
    await session.flush()
    return await button_out(session, user, target.id)


@router.get("/buttons/{button_id}/webhook-logs")
async def webhook_logs(
    button_id: int, user: CurrentUser, session: DbSession
) -> list[WebhookLogOut]:
    """Last 20 webhook attempts, newest first — for debugging a hook."""
    button = await get_button(session, user, button_id)
    return list(
        await session.scalars(
            select(WebhookLog)
            .where(WebhookLog.button_id == button.id)
            .order_by(WebhookLog.requested_at.desc(), WebhookLog.id.desc())
            .limit(20)
        )
    )
