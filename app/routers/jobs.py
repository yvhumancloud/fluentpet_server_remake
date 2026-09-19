"""Maintenance work exposed over HTTP (X-Job-Key), triggered by hand instead of a scheduler."""

from fastapi import APIRouter, Depends

from app.auth import require_job_key
from app.jobs import base_offline, weekly_digest

router = APIRouter(
    prefix="/internal",
    tags=["internal"],
    dependencies=[Depends(require_job_key)],
    include_in_schema=False,
)


@router.post("/base-offline")
async def run_base_offline() -> dict[str, int]:
    return {"pushed": await base_offline.run()}


@router.post("/weekly-digest")
async def run_weekly_digest() -> dict[str, int]:
    return await weekly_digest.run()
