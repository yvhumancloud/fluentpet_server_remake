import pytest

from app.auth import require_device_key
from app.errors import Unauthenticated


async def test_healthz(client):
    r = await client.get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert r.headers["x-request-id"]


async def test_error_envelope(client):
    r = await client.get("/api/v1/me")
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "unauthenticated", "message": "missing bearer token"}}
    r = await client.get("/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


async def test_first_sign_in_provisions_user_household_and_pusher(client, as_user):
    as_user("uid1", "Ann@Example.com", name="Ann")
    r = await client.get("/api/v1/me")
    assert r.status_code == 200
    me = r.json()
    assert me["email"] == "ann@example.com"
    assert me["is_household_admin"] is True
    assert me["household"]["name"].startswith("FLUENT") and len(me["household"]["name"]) == 11
    assert [(p["name"], p["is_human"]) for p in me["pushers"]] == [("Ann", True)]

    # second sign-in returns the same user, no second household
    r2 = await client.get("/api/v1/me")
    assert r2.json()["id"] == me["id"]
    assert r2.json()["household"]["id"] == me["household"]["id"]


async def test_pusher_name_falls_back_to_email_local_part(client, as_user):
    as_user("uid2", "bob.smith@example.com")
    me = (await client.get("/api/v1/me")).json()
    assert me["full_name"] is None
    assert me["pushers"][0]["name"] == "bob.smith"


async def test_login_as(client, as_user):
    as_user("uid-target", "target@example.com")
    target = (await client.get("/api/v1/me")).json()

    as_user("uid-plain", "plain@example.com")
    r = await client.get("/api/v1/me", headers={"X-Login-As": "target@example.com"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"

    as_user("uid-admin", "admin@example.com", admin=True)
    for _ in range(2):  # second call hits the log upsert's conflict path
        r = await client.get("/api/v1/me", headers={"X-Login-As": "Target@example.com"})
        assert r.status_code == 200 and r.json()["id"] == target["id"]
    r = await client.get("/api/v1/me", headers={"X-Login-As": "ghost@example.com"})
    assert r.status_code == 404


def test_device_key():
    require_device_key("test-device-key")
    with pytest.raises(Unauthenticated):
        require_device_key("wrong")
    with pytest.raises(Unauthenticated):
        require_device_key(None)
