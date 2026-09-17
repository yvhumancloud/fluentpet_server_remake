from fastapi import APIRouter
from sqlalchemy import delete, func, select

from app.auth import CurrentUser, DbSession, delete_firebase_user
from app.errors import Conflict
from app.models import Household, HouseholdInvitation, Preference, Pusher, User
from app.schemas import InvitationOut, MeOut, MePatch, UserOut

router = APIRouter(tags=["me"])


async def invitation_out(session: DbSession, inv: HouseholdInvitation) -> InvitationOut:
    return (await invitations_out(session, [inv]))[0]


async def invitations_out(
    session: DbSession, invs: list[HouseholdInvitation]
) -> list[InvitationOut]:
    """Attach household name and inviter email (PRD: invitations are delivered in-app)."""
    if not invs:
        return []
    rows = await session.execute(
        select(HouseholdInvitation.id, Household.name, User.email)
        .join(Household, Household.id == HouseholdInvitation.household_id)
        .join(User, User.id == HouseholdInvitation.inviting_user_id)
        .where(HouseholdInvitation.id.in_([i.id for i in invs]))
    )
    extra = {id_: (name, email) for id_, name, email in rows}
    return [
        InvitationOut(
            **{c: getattr(i, c) for c in ("id", "household_id", "email", "status", "expires_at")},
            household_name=extra[i.id][0],
            invited_by=extra[i.id][1],
        )
        for i in invs
    ]


async def me_out(session: DbSession, user: User) -> MeOut:
    household = await session.get_one(Household, user.household_id)
    pushers = await session.scalars(
        select(Pusher)
        .where(Pusher.household_id == user.household_id)
        .order_by(Pusher.interactions_count.desc(), Pusher.id)
    )
    flags = await session.scalar(
        select(Preference.value).where(
            Preference.user_id == user.id, Preference.key == "feature_flags"
        )
    )
    invitations = await session.scalars(
        select(HouseholdInvitation)
        .where(
            HouseholdInvitation.email == user.email,
            HouseholdInvitation.status == "pending",
            HouseholdInvitation.expires_at > func.now(),
        )
        .order_by(HouseholdInvitation.id)
    )
    return MeOut(
        **UserOut.model_validate(user).model_dump(),
        household=household,
        pushers=list(pushers),
        feature_flags=flags or {},
        pending_invitations=await invitations_out(session, list(invitations)),
    )


@router.get("/me")
async def get_me(user: CurrentUser, session: DbSession) -> MeOut:
    return await me_out(session, user)


@router.patch("/me")
async def patch_me(body: MePatch, user: CurrentUser, session: DbSession) -> MeOut:
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(user, k, v)
    await session.flush()
    return await me_out(session, user)


@router.delete("/me", status_code=204)
async def delete_me(user: CurrentUser, session: DbSession) -> None:
    """Play Store account deletion. A sole admin takes the whole household with them."""
    others = await session.scalar(
        select(User.id).where(User.household_id == user.household_id, User.id != user.id)
    )
    if user.is_household_admin and others:
        raise Conflict("remove other members from your household first")
    name = user.full_name or user.email.split("@")[0]
    await session.execute(
        delete(Pusher).where(
            Pusher.household_id == user.household_id,
            Pusher.is_human,
            func.lower(Pusher.name) == name.lower(),
        )
    )
    await session.delete(user)
    if user.is_household_admin:
        await session.execute(delete(Household).where(Household.id == user.household_id))
    await session.flush()
    delete_firebase_user(user.firebase_uid)
