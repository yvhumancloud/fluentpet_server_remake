from typing import Literal

from fastapi import APIRouter
from sqlalchemy import func, or_, select

from app.auth import CurrentUser, DbSession
from app.errors import Conflict
from app.models import Context
from app.schemas import ContextCreate, ContextOut

router = APIRouter(tags=["contexts"])


@router.get("/contexts")
async def list_contexts(
    kind: Literal["teacher", "learner", "custom"], user: CurrentUser, session: DbSession
) -> list[ContextOut]:
    where = {
        "teacher": (Context.household_id.is_(None), Context.applies_to != "learner"),
        "learner": (Context.household_id.is_(None), Context.applies_to != "human"),
        "custom": (Context.household_id == user.household_id,),
    }[kind]
    return list(await session.scalars(select(Context).where(*where).order_by(Context.text)))


@router.post("/contexts", status_code=201)
async def create_context(body: ContextCreate, user: CurrentUser, session: DbSession) -> ContextOut:
    taken = await session.scalar(
        select(Context.id).where(
            or_(Context.household_id.is_(None), Context.household_id == user.household_id),
            func.lower(Context.text) == body.text.lower(),
        )
    )
    if taken:
        raise Conflict("a context with that text already exists")
    context = Context(household_id=user.household_id, **body.model_dump())
    session.add(context)
    await session.flush()
    return context
