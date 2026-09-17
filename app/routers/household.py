from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Response
from sqlalchemy import func, select, update

from app.auth import CurrentUser, DbSession, HouseholdAdmin, move_to_fresh_household
from app.errors import Conflict, NotFound
from app.models import Household, HouseholdInvitation, User
from app.routers.me import invitation_out, invitations_out, me_out
from app.schemas import (
    HouseholdDetailOut,
    InvitationCreate,
    InvitationOut,
    InvitationsOut,
    MeOut,
)

router = APIRouter(prefix="/household", tags=["household"])
INVITATION_TTL = timedelta(hours=72)


@router.get("")
async def get_household(user: CurrentUser, session: DbSession) -> HouseholdDetailOut:
    hh = await session.get_one(Household, user.household_id)
    members = await session.scalars(
        select(User)
        .where(User.household_id == hh.id)
        .order_by(User.is_household_admin.desc(), User.id)
    )
    invitations = await session.scalars(_pending(HouseholdInvitation.household_id == hh.id))
    return HouseholdDetailOut(
        id=hh.id,
        name=hh.name,
        members=list(members),
        invitations=await invitations_out(session, list(invitations)),
    )


@router.post("/invitations", status_code=201)
async def create_invitation(
    body: InvitationCreate, admin: HouseholdAdmin, session: DbSession, response: Response
) -> InvitationOut:
    email = body.email.lower()
    member = await session.scalar(
        select(User.id).where(User.email == email, User.household_id == admin.household_id)
    )
    if member:
        raise Conflict("already a member of this household")
    existing = await session.scalar(
        select(HouseholdInvitation).where(
            HouseholdInvitation.household_id == admin.household_id,
            HouseholdInvitation.email == email,
            HouseholdInvitation.status == "pending",
        )
    )
    if existing:
        response.status_code = 200
        return await invitation_out(session, existing)
    inv = HouseholdInvitation(
        household_id=admin.household_id,
        inviting_user_id=admin.id,
        email=email,
        expires_at=datetime.now(UTC) + INVITATION_TTL,
    )
    session.add(inv)
    await session.flush()
    return await invitation_out(session, inv)


def _pending(*where):
    return (
        select(HouseholdInvitation)
        .where(
            HouseholdInvitation.status == "pending",
            HouseholdInvitation.expires_at > func.now(),
            *where,
        )
        .order_by(HouseholdInvitation.id)
    )


@router.get("/invitations")
async def list_invitations(user: CurrentUser, session: DbSession) -> InvitationsOut:
    sent = []
    if user.is_household_admin:
        sent = list(
            await session.scalars(_pending(HouseholdInvitation.household_id == user.household_id))
        )
    received = list(await session.scalars(_pending(HouseholdInvitation.email == user.email)))
    return InvitationsOut(
        sent=await invitations_out(session, sent), received=await invitations_out(session, received)
    )


async def _received(session: DbSession, user: User, invitation_id: int) -> HouseholdInvitation:
    inv = await session.scalar(
        _pending(HouseholdInvitation.id == invitation_id, HouseholdInvitation.email == user.email)
    )
    if inv is None:
        raise NotFound("invitation not found")
    return inv


async def _other_members(session: DbSession, user: User) -> bool:
    return bool(
        await session.scalar(
            select(User.id).where(User.household_id == user.household_id, User.id != user.id)
        )
    )


@router.post("/invitations/{invitation_id}/accept")
async def accept_invitation(invitation_id: int, user: CurrentUser, session: DbSession) -> MeOut:
    inv = await _received(session, user, invitation_id)
    if user.is_household_admin and await _other_members(session, user):
        raise Conflict("remove other members from your household first")
    inv.status = "accepted"
    await session.execute(
        update(HouseholdInvitation)
        .where(HouseholdInvitation.email == user.email, HouseholdInvitation.status == "pending")
        .values(status="rejected")
    )
    user.household_id = inv.household_id
    user.is_household_admin = False
    await session.flush()
    return await me_out(session, user)


@router.post("/invitations/{invitation_id}/reject", status_code=204)
async def reject_invitation(invitation_id: int, user: CurrentUser, session: DbSession) -> None:
    inv = await _received(session, user, invitation_id)
    inv.status = "rejected"


@router.delete("/invitations/{invitation_id}", status_code=204)
async def delete_invitation(invitation_id: int, admin: HouseholdAdmin, session: DbSession) -> None:
    inv = await session.scalar(
        _pending(
            HouseholdInvitation.id == invitation_id,
            HouseholdInvitation.household_id == admin.household_id,
        )
    )
    if inv is None:
        raise NotFound("invitation not found")
    await session.delete(inv)


@router.post("/leave")
async def leave_household(user: CurrentUser, session: DbSession) -> MeOut:
    if user.is_household_admin:
        raise Conflict(
            "remove other members first"
            if await _other_members(session, user)
            else "you are the only member of your household"
        )
    await move_to_fresh_household(session, user)
    return await me_out(session, user)


@router.delete("/members/{user_id}", status_code=204)
async def remove_member(user_id: int, admin: HouseholdAdmin, session: DbSession) -> None:
    if user_id == admin.id:
        raise Conflict("use /household/leave to leave your own household")
    member = await session.scalar(
        select(User).where(User.id == user_id, User.household_id == admin.household_id)
    )
    if member is None:
        raise NotFound("member not found")
    await move_to_fresh_household(session, member)
