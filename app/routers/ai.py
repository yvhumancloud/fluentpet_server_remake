from fastapi import APIRouter

from app.auth import CurrentUser, DbSession
from app.schemas import ChatIn, ChatOut, LogTextIn, LogTextOut
from app.services import ai as svc

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/log-text")
async def log_text(body: LogTextIn, user: CurrentUser, session: DbSession) -> LogTextOut:
    """A draft interaction from free text. Nothing is written: the app confirms, then posts it."""
    await svc.check_limit(session, user, "log_text")
    draft, unmatched = await svc.log_text(session, user, body.text)
    return LogTextOut(draft=draft, unmatched_words=unmatched)


@router.post("/chat")
async def chat(body: ChatIn, user: CurrentUser, session: DbSession) -> ChatOut:
    """One turn. The app sends the whole thread back each time; nothing is stored but ai_log."""
    remaining = await svc.check_limit(session, user, "chat")
    reply = await svc.chat(session, user, [m.model_dump() for m in body.messages])
    return ChatOut(reply=reply, remaining_today=remaining)
