from fastapi import APIRouter
from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert

from app.auth import CurrentUser, DbSession
from app.errors import NotFound
from app.models import PushToken
from app.schemas import PushTokenIn

router = APIRouter(prefix="/push-tokens", tags=["push"])


@router.put("", status_code=204)
async def register_push_token(body: PushTokenIn, user: CurrentUser, session: DbSession) -> None:
    values = {"user_id": user.id, "platform": body.platform, "updated_at": func.now()}
    await session.execute(
        insert(PushToken)
        .values(token=body.token, **values)
        .on_conflict_do_update(index_elements=["token"], set_=values)
    )


@router.delete("/{token}", status_code=204)
async def delete_push_token(token: str, user: CurrentUser, session: DbSession) -> None:
    result = await session.execute(
        delete(PushToken).where(PushToken.token == token, PushToken.user_id == user.id)
    )
    if result.rowcount == 0:
        raise NotFound("push token not found")
