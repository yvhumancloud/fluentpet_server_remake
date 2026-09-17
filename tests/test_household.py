from datetime import UTC, datetime, timedelta


async def test_get_household_shows_members_and_admin(client, as_user):
    as_user("u1", "ann@example.com", name="Ann")
    r = await client.get("/api/v1/household")
    assert r.status_code == 200
    body = r.json()
    assert body["name"].startswith("FLUENT")
    assert [(m["email"], m["is_household_admin"]) for m in body["members"]] == [
        ("ann@example.com", True)
    ]
    assert body["invitations"] == []


async def test_admin_invites_by_email_idempotently(client, as_user):
    as_user("u1", "ann@example.com")
    r = await client.post("/api/v1/household/invitations", json={"email": "Bob@Example.com"})
    assert r.status_code == 201
    inv = r.json()
    assert inv["email"] == "bob@example.com" and inv["status"] == "pending"
    expires = datetime.fromisoformat(inv["expires_at"])
    assert timedelta(hours=71) < expires - datetime.now(UTC) <= timedelta(hours=72)

    again = await client.post("/api/v1/household/invitations", json={"email": "bob@example.com"})
    assert again.status_code == 200 and again.json()["id"] == inv["id"]

    hh = (await client.get("/api/v1/household")).json()
    assert [i["email"] for i in hh["invitations"]] == ["bob@example.com"]


async def test_invite_rejects_self_and_bad_email(client, as_user):
    as_user("u1", "ann@example.com")
    r = await client.post("/api/v1/household/invitations", json={"email": "ann@example.com"})
    assert r.status_code == 409
    r = await client.post("/api/v1/household/invitations", json={"email": "not-an-email"})
    assert r.status_code == 422


async def test_invitee_sees_and_accepts_invitation(client, as_user):
    as_user("ann", "ann@example.com", name="Ann")
    ann_hh = (await client.get("/api/v1/me")).json()["household"]
    inv = (await client.post("/api/v1/household/invitations", json={"email": "bob@x.com"})).json()

    as_user("bob", "bob@x.com", name="Bob")
    r = await client.get("/api/v1/household/invitations")
    assert r.status_code == 200
    assert r.json()["sent"] == []
    assert [i["id"] for i in r.json()["received"]] == [inv["id"]]

    r = await client.post(f"/api/v1/household/invitations/{inv['id']}/accept")
    assert r.status_code == 200
    me = r.json()
    assert me["household"]["id"] == ann_hh["id"] and me["is_household_admin"] is False

    as_user("ann", "ann@example.com")
    hh = (await client.get("/api/v1/household")).json()
    assert sorted(m["email"] for m in hh["members"]) == ["ann@example.com", "bob@x.com"]
    assert hh["invitations"] == []
    sent = (await client.get("/api/v1/household/invitations")).json()["sent"]
    assert sent == []


async def test_accept_rejects_other_pending_invitations(client, as_user):
    as_user("ann", "ann@example.com")
    inv_a = (await client.post("/api/v1/household/invitations", json={"email": "bob@x.com"})).json()
    as_user("cat", "cat@example.com")
    inv_c = (await client.post("/api/v1/household/invitations", json={"email": "bob@x.com"})).json()

    as_user("bob", "bob@x.com")
    await client.post(f"/api/v1/household/invitations/{inv_a['id']}/accept")
    assert (await client.get("/api/v1/household/invitations")).json()["received"] == []
    r = await client.post(f"/api/v1/household/invitations/{inv_c['id']}/accept")
    assert r.status_code == 404


async def test_reject_invitation(client, as_user):
    as_user("ann", "ann@example.com")
    inv = (await client.post("/api/v1/household/invitations", json={"email": "bob@x.com"})).json()
    as_user("bob", "bob@x.com")
    r = await client.post(f"/api/v1/household/invitations/{inv['id']}/reject")
    assert r.status_code == 204
    assert (await client.get("/api/v1/household/invitations")).json()["received"] == []
    # someone else's invitation is invisible
    as_user("eve", "eve@x.com")
    r = await client.post(f"/api/v1/household/invitations/{inv['id']}/reject")
    assert r.status_code == 404


async def test_admin_with_members_cannot_accept_elsewhere(client, as_user):
    as_user("ann", "ann@example.com")
    inv = (await client.post("/api/v1/household/invitations", json={"email": "bob@x.com"})).json()
    as_user("bob", "bob@x.com")
    await client.post(f"/api/v1/household/invitations/{inv['id']}/accept")

    as_user("cat", "cat@example.com")
    inv2 = (
        await client.post("/api/v1/household/invitations", json={"email": "ann@example.com"})
    ).json()
    as_user("ann", "ann@example.com")
    r = await client.post(f"/api/v1/household/invitations/{inv2['id']}/accept")
    assert r.status_code == 409


async def test_admin_deletes_invitation(client, as_user):
    as_user("ann", "ann@example.com")
    inv = (await client.post("/api/v1/household/invitations", json={"email": "bob@x.com"})).json()
    r = await client.delete(f"/api/v1/household/invitations/{inv['id']}")
    assert r.status_code == 204
    assert (await client.get("/api/v1/household")).json()["invitations"] == []
    assert (await client.delete(f"/api/v1/household/invitations/{inv['id']}")).status_code == 404


async def _join(client, as_user, admin_email, member_uid, member_email):
    as_user(f"uid-{admin_email}", admin_email)
    inv = (await client.post("/api/v1/household/invitations", json={"email": member_email})).json()
    as_user(member_uid, member_email, name=member_uid.title())
    await client.post(f"/api/v1/household/invitations/{inv['id']}/accept")


async def test_member_leaves_to_fresh_household(client, as_user):
    await _join(client, as_user, "ann@example.com", "bob", "bob@x.com")
    old = (await client.get("/api/v1/me")).json()["household"]["id"]

    r = await client.post("/api/v1/household/leave")
    assert r.status_code == 200
    me = r.json()
    assert me["household"]["id"] != old and me["is_household_admin"] is True
    assert [p["name"] for p in me["pushers"]] == ["Bob"]

    as_user("uid-ann@example.com", "ann@example.com")
    members = (await client.get("/api/v1/household")).json()["members"]
    assert [m["email"] for m in members] == ["ann@example.com"]


async def test_admin_cannot_leave_with_members(client, as_user):
    await _join(client, as_user, "ann@example.com", "bob", "bob@x.com")
    as_user("uid-ann@example.com", "ann@example.com")
    assert (await client.post("/api/v1/household/leave")).status_code == 409

    as_user("solo", "solo@x.com")
    r = await client.post("/api/v1/household/leave")
    assert r.status_code == 409  # sole admin has nowhere to go; leaving is a no-op


async def test_admin_removes_member(client, as_user):
    await _join(client, as_user, "ann@example.com", "bob", "bob@x.com")
    bob_id = (await client.get("/api/v1/me")).json()["id"]

    r = await client.delete(f"/api/v1/household/members/{bob_id}")
    assert r.status_code == 403  # bob is not admin

    as_user("uid-ann@example.com", "ann@example.com")
    ann_id = (await client.get("/api/v1/me")).json()["id"]
    r = await client.delete(f"/api/v1/household/members/{bob_id}")
    assert r.status_code == 204
    assert [m["email"] for m in (await client.get("/api/v1/household")).json()["members"]] == [
        "ann@example.com"
    ]
    assert (await client.delete(f"/api/v1/household/members/{bob_id}")).status_code == 404
    assert (await client.delete(f"/api/v1/household/members/{ann_id}")).status_code == 409

    as_user("bob", "bob@x.com")
    me = (await client.get("/api/v1/me")).json()
    assert me["is_household_admin"] is True and [p["name"] for p in me["pushers"]] == ["Bob"]
