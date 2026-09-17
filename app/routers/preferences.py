from typing import Any

from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.auth import CurrentUser, DbSession
from app.errors import ApiError, NotFound
from app.models import PREFERENCE_KEYS, Preference, Pusher, User
from app.schemas import PUSH_FREQUENCIES, PreferenceIn, PreferenceOut

router = APIRouter(prefix="/preferences", tags=["preferences"])


class Invalid(ApiError):
    status = 422
    code = "validation_error"


async def _validate(session: DbSession, user: User, key: str, value: Any) -> None:
    match key:
        case "push_frequency" if value not in PUSH_FREQUENCIES:
            raise Invalid(f"push_frequency must be one of {', '.join(PUSH_FREQUENCIES)}")
        case "default_pusher_id":
            ok = isinstance(value, int) and await session.scalar(
                select(Pusher.id).where(
                    Pusher.id == value, Pusher.household_id == user.household_id, ~Pusher.is_hidden
                )
            )
            if not ok:
                raise Invalid("default_pusher_id must be a visible pusher in your household")
        case "feature_flags" if not isinstance(value, dict):
            raise Invalid("feature_flags must be an object")
        case "activity_sort" | "button_sort" if not isinstance(value, str):
            raise Invalid(f"{key} must be a string")


@router.get("")
async def get_preferences(user: CurrentUser, session: DbSession) -> dict[str, Any]:
    rows = await session.execute(
        select(Preference.key, Preference.value).where(Preference.user_id == user.id)
    )
    return dict(rows.all())


@router.put("/{key}")
async def put_preference(
    key: str, body: PreferenceIn, user: CurrentUser, session: DbSession
) -> PreferenceOut:
    if key not in PREFERENCE_KEYS:
        raise NotFound("unknown preference key")
    await _validate(session, user, key, body.value)
    await session.execute(
        insert(Preference)
        .values(user_id=user.id, key=key, value=body.value)
        .on_conflict_do_update(index_elements=["user_id", "key"], set_={"value": body.value})
    )
    return PreferenceOut(key=key, value=body.value)
