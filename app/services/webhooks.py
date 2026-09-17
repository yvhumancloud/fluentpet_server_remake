"""Button webhooks: one GET per new base press, after commit. `get` is the test boundary."""

import asyncio
import ipaddress
import logging
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from app.db import SessionLocal
from app.models import WebhookLog

log = logging.getLogger("fluentpet.webhooks")
ATTEMPTS = 3
TIMEOUT = 5


async def get(url: str) -> int:
    await _assert_public(url)
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
        return (await client.get(url)).status_code


async def _assert_public(url: str) -> None:
    """The URL was checked syntactically on save; here the name must also resolve publicly."""
    # ponytail: resolve-then-connect leaves a DNS-rebinding window; pin the IP if it ever matters.
    host = urlsplit(url).hostname or ""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    if not infos or any(not ipaddress.ip_address(i[4][0]).is_global for i in infos):
        raise ValueError(f"{host} does not resolve to a public address")


async def call(button_id: int, url: str) -> None:
    async with SessionLocal() as session, session.begin():
        for _ in range(ATTEMPTS):
            row = WebhookLog(button_id=button_id, url=url, requested_at=datetime.now(UTC))
            try:
                row.status_code = await get(url)
                row.responded_at = datetime.now(UTC)
            except Exception as e:  # timeouts, DNS, refused: logged, retried
                log.warning("webhook %s failed: %s", url, e)
            session.add(row)
            if row.status_code is not None:
                return
