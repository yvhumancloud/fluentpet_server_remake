from datetime import UTC, datetime, timedelta

from app.jobs import base_offline
from tests.test_device_events import DEVICE

API = "/api/v1"


def ago(hours: int) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


async def test_base_offline_push_once_per_outage(client, as_user, fcm):
    as_user("ann", "ann@example.com", name="Ann")
    await client.put(f"{API}/push-tokens", json={"token": "ann-phone"})
    for serial, hours in (("AAAAAAAAAAAA", 40), ("BBBBBBBBBBBB", 10), ("CCCCCCCCCCCC", 200)):
        await client.post(f"{API}/bases", json={"serial_number": serial, "name": serial[:3]})
        ev = {"serial_number": serial, "type": "online", "occurred_at": ago(hours)}
        await client.post(f"{API}/device/events", json=[ev], headers=DEVICE)
    as_user("dan", "dan@y.com", name="Dan")  # another household, never online
    await client.put(f"{API}/push-tokens", json={"token": "dan-phone"})
    await client.post(f"{API}/bases", json={"serial_number": "DDDDDDDDDDDD", "name": "D"})
    fcm.sent.clear()

    assert await base_offline.run() == 1
    assert fcm.keys("ann-phone") == ["base_offline"] and fcm.keys("dan-phone") == []
    assert fcm.sent[0]["body"] == "AAA has been offline for over a day"

    assert await base_offline.run() == 0  # same outage: no repeat
    assert fcm.keys("ann-phone") == ["base_offline"]


async def test_base_offline_job_endpoint(client, as_user, fcm, monkeypatch):
    monkeypatch.setattr("app.auth.settings.job_api_key", "job-secret")
    as_user("ann", "ann@example.com", name="Ann")
    await client.put(f"{API}/push-tokens", json={"token": "ann-phone"})
    await client.post(f"{API}/bases", json={"serial_number": "AAAAAAAAAAAA", "name": "A"})
    ev = {"serial_number": "AAAAAAAAAAAA", "type": "online", "occurred_at": ago(40)}
    await client.post(f"{API}/device/events", json=[ev], headers=DEVICE)
    fcm.sent.clear()

    assert (await client.post(f"{API}/internal/base-offline")).status_code == 401
    r = await client.post(f"{API}/internal/base-offline", headers={"X-Job-Key": "wrong"})
    assert r.status_code == 401
    r = await client.post(f"{API}/internal/base-offline", headers={"X-Job-Key": "job-secret"})
    assert r.status_code == 200 and r.json() == {"pushed": 1}
    assert fcm.keys("ann-phone") == ["base_offline"]
    assert "/api/v1/internal/base-offline" not in client._transport.app.openapi()["paths"]
