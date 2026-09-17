"""POST /interactions/search: one feed of interactions and notes.

Each side is filtered to (id, type, occurred_at, created_at), UNIONed, counted, sorted, paged;
only the page is hydrated.
"""

from fastapi import APIRouter
from sqlalchemy import Select, and_, exists, false, func, literal, select, union_all

from app.auth import CurrentUser, DbSession
from app.models import BaseButton, ButtonPress, Interaction, InteractionContext, Note, Pusher, User
from app.schemas import NoteOut, SearchCounts, SearchFilters, SearchIn, SearchOut
from app.services import interactions as svc

router = APIRouter(tags=["interactions"])


def _interactions(user: User, f: SearchFilters, tab: str) -> Select:
    q = select(
        Interaction.id,
        literal("interaction").label("type"),
        Interaction.occurred_at,
        Interaction.created_at,
    ).where(Interaction.household_id == user.household_id, Interaction.deleted_at.is_(None))
    if not f.include_hidden:
        q = q.where(~Interaction.is_hidden)
    if tab == "assigned":
        q = q.where(Interaction.pusher_id.is_not(None))
    elif tab == "unassigned":
        q = q.where(Interaction.pusher_id.is_(None))
    if f.notes == "only":
        q = q.where(false())
    if f.text:
        q = q.where(Interaction.note.icontains(f.text, autoescape=True))
    if f.from_:
        q = q.where(Interaction.occurred_at >= f.from_)
    if f.to:
        q = q.where(Interaction.occurred_at <= f.to)
    if f.with_note != "include":
        has_note = Interaction.note.is_not(None)
        q = q.where(has_note if f.with_note == "only" else ~has_note)
    if f.favourites != "include":
        q = q.where(Interaction.is_favourite == (f.favourites == "only"))
    if f.presses == "single":
        q = q.where(Interaction.num_presses == 1)
    elif f.presses == "multiple":
        q = q.where(Interaction.num_presses > 1)
    if f.pusher_ids:
        q = q.where(Interaction.pusher_id.in_(f.pusher_ids))
    if f.button_ids:
        q = q.where(_has(f.match, f.button_ids, ButtonPress.button_id, ButtonPress.interaction_id))
    if f.context_ids:
        q = q.where(
            _has(
                f.match,
                f.context_ids,
                InteractionContext.context_id,
                InteractionContext.interaction_id,
            )
        )
    if f.base_ids:
        q = q.where(
            exists().where(
                ButtonPress.interaction_id == Interaction.id,
                BaseButton.button_id == ButtonPress.button_id,
                BaseButton.base_id.in_(f.base_ids),
            )
        )
    return q


def _has(match: str, ids: list[int], col, link_col):
    """EXISTS a linked row with col in ids (any), or one EXISTS per id (all)."""
    one = lambda cond: exists().where(link_col == Interaction.id, cond)  # noqa: E731
    if match == "all":
        return and_(*(one(col == i) for i in ids))
    return one(col.in_(ids))


def _notes(user: User, f: SearchFilters, tab: str) -> Select | None:
    if tab != "all" or f.interactions_only:
        return None
    q = select(Note.id, literal("note").label("type"), Note.occurred_at, Note.created_at).where(
        Note.household_id == user.household_id, Note.deleted_at.is_(None)
    )
    if not f.include_hidden:
        q = q.where(~Note.is_hidden)
    if f.text:
        q = q.where(Note.text.icontains(f.text, autoescape=True))
    if f.from_:
        q = q.where(Note.occurred_at >= f.from_)
    if f.to:
        q = q.where(Note.occurred_at <= f.to)
    if f.favourites != "include":
        q = q.where(Note.is_favourite == (f.favourites == "only"))
    return q


@router.post("/interactions/search")
async def search(body: SearchIn, user: CurrentUser, session: DbSession) -> SearchOut:
    iq = _interactions(user, body.filters, body.tab)
    nq = _notes(user, body.filters, body.tab)
    feed = union_all(iq, nq).subquery() if nq is not None else iq.subquery()
    order = {
        "occurred_at_desc": (feed.c.occurred_at.desc(), feed.c.created_at.desc()),
        "occurred_at_asc": (feed.c.occurred_at.asc(), feed.c.created_at.asc()),
        "created_at_desc": (feed.c.created_at.desc(), feed.c.id.desc()),
    }[body.sort]
    total = await session.scalar(select(func.count()).select_from(feed))
    page = (
        await session.execute(
            select(feed.c.id, feed.c.type)
            .order_by(*order)
            .limit(body.per_page)
            .offset((body.page - 1) * body.per_page)
        )
    ).all()
    by_kind = await session.execute(
        select(Pusher.is_human, func.count())
        .select_from(Interaction)
        .outerjoin(Pusher, Pusher.id == Interaction.pusher_id)
        .where(Interaction.id.in_(select(iq.subquery().c.id)))
        .group_by(Pusher.is_human)
    )
    kinds = dict(by_kind.all())
    items = {
        ("interaction", i.id): i
        for i in await svc.load(session, [id for id, t in page if t == "interaction"])
    }
    note_ids = [id for id, t in page if t == "note"]
    if note_ids:
        for n in await session.scalars(select(Note).where(Note.id.in_(note_ids))):
            items[("note", n.id)] = NoteOut.model_validate(n)
    return SearchOut(
        items=[items[(t, id)] for id, t in page],
        total=total,
        page=body.page,
        per_page=body.per_page,
        counts=SearchCounts(
            communication=kinds.get(False, 0),
            modeling=kinds.get(True, 0),
            unassigned=kinds.get(None, 0),
        ),
    )
