from tests.test_bases import SERIAL

DEVICE = {"X-Device-Key": "test-device-key"}


def seen(button_serial: str, at: str = "2026-09-17T10:00:00Z", base: str = SERIAL) -> dict:
    return {
        "serial_number": base,
        "button_serial_number": button_serial,
        "type": "button_seen",
        "occurred_at": at,
        "payload": {"rssi": -40},
    }


async def test_device_routes_require_device_key(client, as_user):
    assert (await client.post("/api/v1/device/events", json=[])).status_code == 401
    r = await client.post("/api/v1/device/events", json=[], headers={"X-Device-Key": "nope"})
    assert r.status_code == 401
    as_user("u1", "ann@example.com")  # a user token is not a device key either
    assert (await client.post("/api/v1/device/events", json=[])).status_code == 401


async def test_button_seen_links_new_button(client, as_user, fcm):
    as_user("u1", "ann@example.com")
    await client.put("/api/v1/push-tokens", json={"token": "ann-phone"})
    await client.post("/api/v1/bases", json={"serial_number": SERIAL, "name": "Kitchen"})

    r = await client.post("/api/v1/device/events", json=[seen("abc123")], headers=DEVICE)
    assert r.status_code == 200 and r.json() == [{"status": "created"}]

    buttons = (await client.get("/api/v1/buttons")).json()
    new = next(b for b in buttons if b["origin"] == "connect")
    assert new["text"] == "Button ABC123"
    assert new["base_button"]["button_serial_number"] == "ABC123"
    assert new["base_button"]["last_online_at"] == "2026-09-17T10:00:00Z"
    base = (await client.get("/api/v1/bases")).json()[0]
    assert [(b["button_id"], b["text"]) for b in base["buttons"]] == [(new["id"], "Button ABC123")]
    assert fcm.keys("ann-phone") == ["base_registered", "button_linked"]

    # same event again is a duplicate; a later sighting of a known serial links nothing new
    r = await client.post(
        "/api/v1/device/events",
        json=[seen("ABC123"), seen("ABC123", at="2026-09-17T10:05:00Z")],
        headers=DEVICE,
    )
    assert r.json() == [{"status": "duplicate"}, {"status": "created"}]
    assert len((await client.get("/api/v1/buttons")).json()) == 2
    assert fcm.keys("ann-phone") == ["base_registered", "button_linked"]


async def test_button_seen_on_unknown_base_or_bad_serial(client, as_user):
    as_user("u1", "ann@example.com")
    await client.post("/api/v1/bases", json={"serial_number": SERIAL, "name": "Kitchen"})
    r = await client.post(
        "/api/v1/device/events",
        json=[seen("abc123", base="NOPE00000000"), seen("00000"), seen("ab")],
        headers=DEVICE,
    )
    assert r.status_code == 200
    assert r.json() == [{"status": "unknown_base"}, {"status": "invalid"}, {"status": "invalid"}]
    assert len((await client.get("/api/v1/buttons")).json()) == 1


async def test_same_serial_is_a_different_button_per_household(client, as_user):
    as_user("ann", "ann@example.com")
    await client.post("/api/v1/bases", json={"serial_number": SERIAL, "name": "A"})
    await client.post("/api/v1/device/events", json=[seen("ABC123")], headers=DEVICE)
    as_user("bob", "bob@example.com")
    await client.post("/api/v1/bases", json={"serial_number": "FPB000000002", "name": "B"})
    r = await client.post(
        "/api/v1/device/events", json=[seen("ABC123", base="FPB000000002")], headers=DEVICE
    )
    assert r.json() == [{"status": "created"}]
    assert [
        b["text"] for b in (await client.get("/api/v1/buttons")).json() if b["origin"] == "connect"
    ] == ["Button ABC123"]


def press(button_serial: str, at, base: str = SERIAL) -> dict:
    return {
        "serial_number": base,
        "button_serial_number": button_serial,
        "type": "press",
        "occurred_at": at,
    }


async def feed(client):
    r = await client.post("/api/v1/interactions/search", json={"sort": "occurred_at_asc"})
    return r.json()["items"]


async def test_press_grouping(client, as_user):
    as_user("u1", "ann@example.com", name="Ann")
    rex = (await client.post("/api/v1/pushers", json={"name": "Rex"})).json()
    await client.post("/api/v1/bases", json={"serial_number": SERIAL, "name": "Kitchen"})
    await client.patch(
        f"/api/v1/bases/{SERIAL}", json={"default_pusher_id": rex["id"], "group_window_seconds": 15}
    )
    await client.patch("/api/v1/me", json={"timezone": "Europe/Paris"})

    r = await client.post(
        "/api/v1/device/events",
        json=[press("AAA111", "2026-09-10T08:00:00Z"), press("BBB222", "2026-09-10T08:00:05Z")],
        headers=DEVICE,
    )
    assert [x["status"] for x in r.json()] == ["created", "created"]
    (i,) = await feed(client)
    assert i["origin"] == "base" and i["pusher"]["id"] == rex["id"]
    assert i["device_timezone"] == "Europe/Paris" and i["occurred_at"] == "2026-09-10T08:00:00Z"
    assert [(p["press_order"], p["text"], p["occurred_at"]) for p in i["presses"]] == [
        (0, "Button AAA111", "2026-09-10T08:00:00Z"),
        (1, "Button BBB222", "2026-09-10T08:00:05Z"),
    ]
    assert i["num_presses"] == 2 and i["duration_seconds"] == 5

    # late-arriving press lands in chronological position; same button+second is a duplicate
    r = await client.post(
        "/api/v1/device/events",
        json=[press("AAA111", "2026-09-10T08:00:02Z"), press("BBB222", "2026-09-10T08:00:05.700Z")],
        headers=DEVICE,
    )
    assert [x["status"] for x in r.json()] == ["created", "duplicate"]
    (i,) = await feed(client)
    assert [(p["press_order"], p["text"]) for p in i["presses"]] == [
        (0, "Button AAA111"),
        (1, "Button AAA111"),
        (2, "Button BBB222"),
    ]

    # outside the window: a new interaction; epoch millis accepted; future clamped to now
    r = await client.post(
        "/api/v1/device/events",
        json=[press("AAA111", 1789200000000), press("BBB222", "2999-01-01T00:00:00Z")],
        headers=DEVICE,
    )
    assert [x["status"] for x in r.json()] == ["created", "created"]
    items = await feed(client)
    assert len(items) == 3
    assert items[1]["occurred_at"] == "2026-09-12T08:00:00Z" and items[1]["num_presses"] == 1
    assert items[2]["occurred_at"] < "2100"

    # without a base default pusher, the household's default_pusher_id preference applies
    tom = (await client.post("/api/v1/pushers", json={"name": "Tom"})).json()
    await client.patch(f"/api/v1/bases/{SERIAL}", json={"default_pusher_id": None})
    await client.put("/api/v1/preferences/default_pusher_id", json={"value": tom["id"]})
    await client.post(
        "/api/v1/device/events", json=[press("AAA111", "2026-09-14T08:00:00Z")], headers=DEVICE
    )
    by_tom = next(i for i in await feed(client) if i["occurred_at"].startswith("2026-09-14"))
    assert by_tom["pusher"]["id"] == tom["id"]

    # counters and feed side effects
    assert (await client.get("/api/v1/pushers")).json()[0]["name"] == "Rex"
    counts = (await client.post("/api/v1/interactions/search", json={})).json()["counts"]
    assert counts == {"communication": 4, "modeling": 0, "unassigned": 0}
