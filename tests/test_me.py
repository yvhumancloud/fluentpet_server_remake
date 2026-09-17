async def test_patch_me_updates_profile(client, as_user):
    as_user("u1", "ann@example.com", name="Ann")
    r = await client.patch(
        "/api/v1/me",
        json={"full_name": "Ann B", "timezone": "Asia/Kolkata", "app_version": "1.0.3"},
    )
    assert r.status_code == 200
    assert (r.json()["full_name"], r.json()["timezone"], r.json()["app_version"]) == (
        "Ann B",
        "Asia/Kolkata",
        "1.0.3",
    )
    me = (await client.get("/api/v1/me")).json()
    assert me["timezone"] == "Asia/Kolkata"


async def test_patch_me_rejects_bad_timezone(client, as_user):
    as_user("u1", "ann@example.com")
    r = await client.patch("/api/v1/me", json={"timezone": "Mars/Olympus"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_error"


async def test_delete_me_removes_account_and_firebase_user(client, as_user, monkeypatch):
    deleted = []
    monkeypatch.setattr("app.routers.me.delete_firebase_user", deleted.append)
    as_user("u1", "ann@example.com", name="Ann")
    first = (await client.get("/api/v1/me")).json()

    r = await client.delete("/api/v1/me")
    assert r.status_code == 204 and deleted == ["u1"]

    # same Firebase user signing in again starts from scratch
    again = (await client.get("/api/v1/me")).json()
    assert again["id"] != first["id"] and again["household"]["id"] != first["household"]["id"]


async def test_delete_me_blocked_for_admin_with_members(client, as_user, monkeypatch):
    monkeypatch.setattr("app.routers.me.delete_firebase_user", lambda uid: None)
    as_user("ann", "ann@example.com")
    inv = (await client.post("/api/v1/household/invitations", json={"email": "bob@x.com"})).json()
    as_user("bob", "bob@x.com", name="Bob")
    await client.post(f"/api/v1/household/invitations/{inv['id']}/accept")

    as_user("ann", "ann@example.com")
    assert (await client.delete("/api/v1/me")).status_code == 409

    # a plain member can delete; the household and its other members stay
    as_user("bob", "bob@x.com")
    assert (await client.delete("/api/v1/me")).status_code == 204
    as_user("ann", "ann@example.com")
    members = (await client.get("/api/v1/household")).json()["members"]
    assert [m["email"] for m in members] == ["ann@example.com"]


async def test_me_surfaces_feature_flags_and_pending_invitations(client, as_user):
    as_user("ann", "ann@example.com")
    inv = (await client.post("/api/v1/household/invitations", json={"email": "bob@x.com"})).json()

    as_user("bob", "bob@x.com")
    me = (await client.get("/api/v1/me")).json()
    assert me["feature_flags"] == {}
    assert [i["id"] for i in me["pending_invitations"]] == [inv["id"]]
    assert me["pending_invitations"][0]["household_name"] == inv["household_name"]
    assert me["pending_invitations"][0]["invited_by"] == "ann@example.com"

    await client.put("/api/v1/preferences/feature_flags", json={"value": {"beta": True}})
    await client.post(f"/api/v1/household/invitations/{inv['id']}/reject")
    me = (await client.get("/api/v1/me")).json()
    assert me["feature_flags"] == {"beta": True} and me["pending_invitations"] == []
