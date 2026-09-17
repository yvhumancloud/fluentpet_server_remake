"""Scheduled work, triggered over HTTP (EventBridge API destination) instead of a job runner."""

from fastapi import APIRouter, Depends

from app.auth import require_job_key
from app.jobs import base_offline

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(require_job_key)],
    include_in_schema=False,
)


@router.post("/base-offline")
async def run_base_offline() -> dict[str, int]:
    return {"pushed": await base_offline.run()}
