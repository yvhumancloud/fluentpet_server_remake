import logging
import time
import uuid

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException

from app.errors import ApiError
from app.routers import (
    audios,
    bases,
    buttons,
    contexts,
    device,
    household,
    interactions,
    jobs,
    me,
    notes,
    preferences,
    push_tokens,
    pushers,
    search,
    stats,
)
from app.settings import settings

log = logging.getLogger("fluentpet")
logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","msg":%(message)s}')

if settings.dev_tokens:
    logging.getLogger("fluentpet").warning(
        "DEV_TOKENS set: bearer tokens bypass Firebase (%s)", settings.env
    )
if settings.sentry_dsn.strip():  # SSM cannot store an empty value; blank means off
    sentry_sdk.init(dsn=settings.sentry_dsn.strip(), environment=settings.env)

app = FastAPI(title="FluentPet API", docs_url="/docs")
for r in (
    me,
    household,
    pushers,
    preferences,
    push_tokens,
    buttons,
    audios,
    bases,
    contexts,
    notes,
    interactions,
    search,
    stats,
    device,
    jobs,
):
    app.include_router(r.router, prefix="/api/v1")


def error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


@app.exception_handler(ApiError)
async def api_error(_, exc: ApiError):
    return error(exc.status, exc.code, exc.message)


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException):
    code = {401: "unauthenticated", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
    return error(exc.status_code, code.get(exc.status_code, "error"), str(exc.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(_, exc: RequestValidationError):
    first = exc.errors()[0]
    loc = ".".join(str(p) for p in first["loc"] if p != "body")
    return error(422, "validation_error", f"{loc}: {first['msg']}")


@app.exception_handler(IntegrityError)
async def integrity_error(_, exc: IntegrityError):
    # unique / check / fk violations surface as 409; anything else is a real bug
    return error(409, "conflict", "conflicts with an existing record")


@app.exception_handler(Exception)
async def unhandled(_, exc: Exception):
    log.exception("unhandled")
    return error(500, "internal_error", "internal error")


@app.middleware("http")
async def request_log(request: Request, call_next):
    request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    started = time.perf_counter()
    response = await call_next(request)
    log.info(
        '{"request_id":"%s","method":"%s","path":"%s","status":%d,"ms":%d,"user_id":%s}',
        request.state.request_id,
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
        getattr(request.state, "user_id", "null"),
    )
    response.headers["x-request-id"] = request.state.request_id
    return response


@app.get("/healthz", include_in_schema=False)
async def healthz():
    """Process liveness only. Deliberately no DB ping: the platform polls this every few
    seconds, and touching Neon would keep it awake 24/7 and burn the free tier."""
    return {"ok": True}
