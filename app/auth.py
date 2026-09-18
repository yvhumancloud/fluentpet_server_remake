import json
import logging
import secrets
from functools import cache
from typing import Annotated, Any

import firebase_admin
from fastapi import Depends, Header, Request
from firebase_admin import auth as fb_auth
from firebase_admin import credentials
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import Forbidden, NotFound, Unauthenticated
from app.models import Button, Household, ImpersonationLog, Pusher, User
from app.settings import settings

INAUDIBLE = "inaudible"
log = logging.getLogger(__name__)


@cache
def firebase_app() -> firebase_admin.App:
    cred = credentials.Certificate(json.loads(settings.firebase_credentials_json))
    return firebase_admin.initialize_app(cred)


def delete_firebase_user(uid: str) -> None:
    fb_auth.delete_user(uid, app=firebase_app())


def get_claims(authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Firebase ID token -> claims. Overridden in tests."""
    if not authorization or not authorization.startswith("Bearer "):
        raise Unauthenticated("missing bearer token")
    if dev := settings.dev_claims(authorization[7:]):
        return dev
    try:
        return fb_auth.verify_id_token(authorization[7:], app=firebase_app(), check_revoked=False)
    except (ValueError, fb_auth.InvalidIdTokenError, fb_auth.ExpiredIdTokenError) as e:
        log.warning("token rejected: %s", e)  # also covers a bad FIREBASE_CREDENTIALS_JSON
        raise Unauthenticated("invalid token") from e


async def provision_user(session: AsyncSession, claims: dict[str, Any]) -> User:
    """First sign-in: user + household + human pusher + inaudible button, one transaction."""
    user = User(
        firebase_uid=claims["uid"], email=claims["email"].lower(), full_name=claims.get("name")
    )
    await move_to_fresh_household(session, user)
    return user


async def move_to_fresh_household(session: AsyncSession, user: User) -> None:
    """Make `user` the admin of a brand-new household with their human pusher and `inaudible`."""
    household = Household(name=await _unique_household_name(session))
    session.add(household)
    await session.flush()
    user.household_id = household.id
    user.is_household_admin = True
    name = user.full_name or user.email.split("@")[0]
    session.add_all(
        [
            user,
            Pusher(household_id=household.id, name=name, is_human=True),
            Button(
                household_id=household.id, text=INAUDIBLE, word=INAUDIBLE, normalized_word=INAUDIBLE
            ),
        ]
    )
    await session.flush()


async def _unique_household_name(session: AsyncSession) -> str:
    # ponytail: check-then-insert; a concurrent collision is a 1-in-90000 retry, no savepoint.
    while True:
        name = f"FLUENT{secrets.randbelow(90000) + 10000}"
        if not await session.scalar(select(Household.id).where(Household.name == name)):
            return name


async def current_user(
    request: Request,
    claims: Annotated[dict[str, Any], Depends(get_claims)],
    session: Annotated[AsyncSession, Depends(get_session)],
    x_login_as: Annotated[str | None, Header()] = None,
) -> User:
    if not claims.get("email"):
        raise Unauthenticated("token has no email")
    user = await session.scalar(select(User).where(User.firebase_uid == claims["uid"]))
    if user is None:
        user = await provision_user(session, claims)
    request.state.user_id = user.id
    if not x_login_as or x_login_as.lower() == user.email:
        return user
    if claims.get("admin") is not True:
        raise Forbidden("login-as requires admin")
    target = await session.scalar(select(User).where(User.email == x_login_as.lower()))
    if target is None:
        raise NotFound("login-as user not found")
    request.state.user_id = target.id
    await session.execute(
        insert(ImpersonationLog)
        .values(
            admin_user_id=user.id,
            target_user_id=target.id,
            method=request.method,
            path=request.url.path,
            window_start=func.date_trunc("hour", func.now()),
        )
        .on_conflict_do_update(
            index_elements=ImpersonationLog.__table__.primary_key.columns,
            set_={"count": ImpersonationLog.count + 1},
        )
    )
    return target


def _key_matches(given: str | None, expected: str) -> bool:
    return bool(expected) and secrets.compare_digest((given or "").encode(), expected.encode())


def require_device_key(x_device_key: Annotated[str | None, Header()] = None) -> None:
    if not _key_matches(x_device_key, settings.device_api_key):
        raise Unauthenticated("invalid device key")


def require_job_key(x_job_key: Annotated[str | None, Header()] = None) -> None:
    if not _key_matches(x_job_key, settings.job_api_key):
        raise Unauthenticated("invalid job key")


CurrentUser = Annotated[User, Depends(current_user)]
DbSession = Annotated[AsyncSession, Depends(get_session)]


def household_admin(user: CurrentUser) -> User:
    if not user.is_household_admin:
        raise Forbidden("household admin only")
    return user


HouseholdAdmin = Annotated[User, Depends(household_admin)]
