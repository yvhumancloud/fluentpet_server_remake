SERIAL = "FPB123456789"


async def test_register_list_patch_delete_base(client, as_user, fcm):
    as_user("u1", "ann@example.com")
    await client.put("/api/v1/push-tokens", json={"token": "ann-phone"})
    r = await client.post(
        "/api/v1/bases", json={"serial_number": SERIAL.lower(), "name": "Kitchen"}
    )
    assert r.status_code == 201
    b = r.json()
    assert (b["serial_number"], b["name"], b["group_window_seconds"], b["buttons"]) == (
        SERIAL,
        "Kitchen",
        15,
        [],
    )
    assert fcm.keys("ann-phone") == ["base_registered"]

    # registering my own base again is idempotent
    again = await client.post("/api/v1/bases", json={"serial_number": SERIAL, "name": "Kitchen 2"})
    assert again.status_code == 200 and again.json()["id"] == b["id"]
    assert fcm.keys() == ["base_registered"]

    assert [x["id"] for x in (await client.get("/api/v1/bases")).json()] == [b["id"]]

    rex = (await client.post("/api/v1/pushers", json={"name": "Rex"})).json()
    r = await client.patch(
        f"/api/v1/bases/{SERIAL}",
        json={"name": "Hall", "default_pusher_id": rex["id"], "group_window_seconds": 30},
    )
    assert r.status_code == 200
    assert (r.json()["name"], r.json()["default_pusher_id"], r.json()["group_window_seconds"]) == (
        "Hall",
        rex["id"],
        30,
    )
    assert (
        await client.patch(f"/api/v1/bases/{SERIAL}", json={"group_window_seconds": 5000})
    ).status_code == 422
    assert (
        await client.patch(f"/api/v1/bases/{SERIAL}", json={"default_pusher_id": 999})
    ).status_code == 422

    # hiding the default pusher clears it on the base
    await client.patch(f"/api/v1/pushers/{rex['id']}", json={"is_hidden": True})
    assert (await client.get("/api/v1/bases")).json()[0]["default_pusher_id"] is None

    assert (await client.delete(f"/api/v1/bases/{SERIAL}")).status_code == 204
    assert (await client.get("/api/v1/bases")).json() == []
    assert (await client.delete(f"/api/v1/bases/{SERIAL}")).status_code == 404
    assert fcm.keys("ann-phone") == ["base_registered", "base_removed"]


async def test_serial_format(client, as_user):
    as_user("u1", "ann@example.com")
    for bad in ("SHORT", "FPB1234567890", "FPB12345678!"):
        r = await client.post("/api/v1/bases", json={"serial_number": bad, "name": "x"})
        assert r.status_code == 422, bad


async def test_register_transfers_base_from_other_household(client, as_user, fcm):
    as_user("ann", "ann@example.com")
    await client.put("/api/v1/push-tokens", json={"token": "ann-phone"})
    first = (await client.post("/api/v1/bases", json={"serial_number": SERIAL, "name": "A"})).json()

    as_user("bob", "bob@example.com")
    await client.put("/api/v1/push-tokens", json={"token": "bob-phone"})
    r = await client.post("/api/v1/bases", json={"serial_number": SERIAL, "name": "B"})
    assert r.status_code == 201 and r.json()["id"] != first["id"]
    assert [x["name"] for x in (await client.get("/api/v1/bases")).json()] == ["B"]
    assert fcm.keys("bob-phone") == ["base_registered"]
    assert fcm.keys("ann-phone") == ["base_registered", "base_removed"]

    as_user("ann", "ann@example.com")
    assert (await client.get("/api/v1/bases")).json() == []
    assert (await client.patch(f"/api/v1/bases/{SERIAL}", json={"name": "x"})).status_code == 404


async def test_push_goes_to_every_member_and_prunes_dead_tokens(client, as_user, fcm):
    as_user("ann", "ann@example.com")
    await client.put("/api/v1/push-tokens", json={"token": "ann-phone"})
    await client.put("/api/v1/push-tokens", json={"token": "ann-tablet"})
    inv = (await client.post("/api/v1/household/invitations", json={"email": "bob@x.com"})).json()
    as_user("bob", "bob@x.com")
    await client.put("/api/v1/push-tokens", json={"token": "bob-phone"})
    await client.post(f"/api/v1/household/invitations/{inv['id']}/accept")

    fcm.errors["ann-tablet"] = "UnregisteredError"
    await client.post("/api/v1/bases", json={"serial_number": SERIAL, "name": "A"})
    assert sorted(fcm.sent[0]["tokens"]) == ["ann-phone", "ann-tablet", "bob-phone"]

    await client.delete(f"/api/v1/bases/{SERIAL}")
    assert sorted(fcm.sent[1]["tokens"]) == ["ann-phone", "bob-phone"]  # dead token pruned
    assert (await client.delete("/api/v1/push-tokens/bob-phone")).status_code == 204
    as_user("ann", "ann@example.com")
    assert (await client.delete("/api/v1/push-tokens/ann-tablet")).status_code == 404
