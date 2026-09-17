from fastapi import APIRouter
from sqlalchemy import func, select

from app.auth import CurrentUser, DbSession
from app.errors import NotFound
from app.models import Note, User
from app.schemas import NoteIn, NoteOut, NotePatch

router = APIRouter(tags=["notes"])


async def get_note(session: DbSession, user: User, note_id: int) -> Note:
    note = await session.scalar(
        select(Note).where(
            Note.id == note_id, Note.household_id == user.household_id, Note.deleted_at.is_(None)
        )
    )
    if note is None:
        raise NotFound("note not found")
    return note


@router.post("/notes", status_code=201)
async def create_note(body: NoteIn, user: CurrentUser, session: DbSession) -> NoteOut:
    note = Note(household_id=user.household_id, created_by_user_id=user.id, **body.model_dump())
    session.add(note)
    await session.flush()
    return note


@router.patch("/notes/{note_id}")
async def patch_note(
    note_id: int, body: NotePatch, user: CurrentUser, session: DbSession
) -> NoteOut:
    note = await get_note(session, user, note_id)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(note, k, v)
    await session.flush()
    return note


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(note_id: int, user: CurrentUser, session: DbSession) -> None:
    note = await get_note(session, user, note_id)
    note.deleted_at = func.now()
