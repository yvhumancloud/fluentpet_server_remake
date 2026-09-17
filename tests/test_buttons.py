async def test_new_household_has_inaudible_button(client, as_user):
    as_user("u1", "ann@example.com")
    r = await client.get("/api/v1/buttons")
    assert r.status_code == 200
    assert [(b["text"], b["origin"]) for b in r.json()] == [("inaudible", "app")]


async def test_create_button_derives_words(client, as_user):
    as_user("u1", "ann@example.com")
    r = await client.post("/api/v1/buttons", json={"text": "  Walkies!  ", "note": "front door"})
    assert r.status_code == 201
    b = r.json()
    assert (b["text"], b["word"], b["normalized_word"], b["note"]) == (
        "Walkies!",
        "walkies",
        "walk",
        "front door",
    )
    assert b["is_hidden"] is False and b["audio_id"] is None and b["base_button"] is None
    assert b["press_count"] == 0
    got = await client.get(f"/api/v1/buttons/{b['id']}")
    assert got.status_code == 200 and got.json()["id"] == b["id"]


async def test_duplicate_text_unhides_existing(client, as_user):
    as_user("u1", "ann@example.com")
    play = (await client.post("/api/v1/buttons", json={"text": "Play"})).json()
    assert (await client.post("/api/v1/buttons", json={"text": "play"})).status_code == 409

    await client.patch(f"/api/v1/buttons/{play['id']}", json={"is_hidden": True})
    assert play["id"] not in [b["id"] for b in (await client.get("/api/v1/buttons")).json()]
    hidden = (await client.get("/api/v1/buttons", params={"include_hidden": "true"})).json()
    assert any(b["id"] == play["id"] and b["is_hidden"] for b in hidden)

    r = await client.post("/api/v1/buttons", json={"text": "PLAY"})
    assert r.status_code == 200 and r.json()["id"] == play["id"] and not r.json()["is_hidden"]


async def test_list_sorts(client, as_user):
    as_user("u1", "ann@example.com")
    for t in ("Zebra", "apple", "Mango"):
        await client.post("/api/v1/buttons", json={"text": t})
    texts = lambda r: [b["text"] for b in r.json()]  # noqa: E731
    assert texts(await client.get("/api/v1/buttons", params={"sort": "alphabet"})) == [
        "apple",
        "inaudible",
        "Mango",
        "Zebra",
    ]
    assert texts(await client.get("/api/v1/buttons", params={"sort": "date"})) == [
        "Mango",
        "apple",
        "Zebra",
        "inaudible",
    ]
    assert (await client.get("/api/v1/buttons", params={"sort": "sideways"})).status_code == 422


async def test_patch_and_soft_delete(client, as_user):
    as_user("u1", "ann@example.com")
    concepts = (await client.get("/api/v1/button-concepts")).json()
    assert len(concepts) == 131 and concepts[0]["concept"] == "ALL DONE"
    b = (await client.post("/api/v1/buttons", json={"text": "Out"})).json()

    r = await client.patch(
        f"/api/v1/buttons/{b['id']}",
        json={
            "text": "Outside (back door)",
            "introduced_at": "2026-01-15",
            "button_concept_id": concepts[0]["id"],
            "webhook_url": "https://example.com/hook",
        },
    )
    assert r.status_code == 200
    j = r.json()
    assert (j["text"], j["word"], j["introduced_at"], j["button_concept_id"]) == (
        "Outside (back door)",
        "outside",
        "2026-01-15",
        concepts[0]["id"],
    )
    assert j["webhook_url"] == "https://example.com/hook"

    assert (await client.delete(f"/api/v1/buttons/{b['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/buttons/{b['id']}")).status_code == 404
    assert b["id"] not in [
        x["id"]
        for x in (await client.get("/api/v1/buttons", params={"include_hidden": "true"})).json()
    ]
    # the text is free again after soft delete
    assert (
        await client.post("/api/v1/buttons", json={"text": "Outside (back door)"})
    ).status_code == 201


async def test_buttons_are_household_scoped(client, as_user):
    as_user("u1", "ann@example.com")
    b = (await client.post("/api/v1/buttons", json={"text": "Out"})).json()
    as_user("u2", "eve@example.com")
    assert (await client.get(f"/api/v1/buttons/{b['id']}")).status_code == 404
    assert (await client.patch(f"/api/v1/buttons/{b['id']}", json={"text": "x"})).status_code == 404
    assert (await client.delete(f"/api/v1/buttons/{b['id']}")).status_code == 404


# ---- linked buttons (link created through the device route) ------------------

from tests.test_bases import SERIAL  # noqa: E402
from tests.test_device_events import DEVICE, seen  # noqa: E402


async def _linked(client, as_user, serial="ABC123"):
    as_user("u1", "ann@example.com")
    await client.put("/api/v1/push-tokens", json={"token": "ann-phone"})
    await client.post("/api/v1/bases", json={"serial_number": SERIAL, "name": "Kitchen"})
    await client.post("/api/v1/device/events", json=[seen(serial)], headers=DEVICE)
    buttons = (await client.get("/api/v1/buttons")).json()
    return next(b for b in buttons if b["origin"] == "connect")


async def test_setting_audio_on_linked_button_bumps_desired_version(client, as_user):
    from tests.test_audios import opus

    b = await _linked(client, as_user)
    assert (b["base_button"]["desired_version"], b["base_button"]["desired_audio_id"]) == (1, None)
    a = (
        await client.post(
            "/api/v1/audios", data={"name": "walk"}, files={"file": ("w.ogg", opus())}
        )
    ).json()
    r = await client.patch(f"/api/v1/buttons/{b['id']}", json={"audio_id": a["id"]})
    bb = r.json()["base_button"]
    assert (bb["desired_version"], bb["desired_audio_id"]) == (2, a["id"])
    # unrelated edits don't bump
    r = await client.patch(f"/api/v1/buttons/{b['id']}", json={"note": "hi"})
    assert r.json()["base_button"]["desired_version"] == 2
    # dropping the audio bumps again
    r = await client.patch(f"/api/v1/buttons/{b['id']}", json={"audio_id": None})
    bb = r.json()["base_button"]
    assert (bb["desired_version"], bb["desired_audio_id"]) == (3, None)
    # deleting an audio in use detaches it from the button and the base
    await client.patch(f"/api/v1/buttons/{b['id']}", json={"audio_id": a["id"]})
    await client.delete(f"/api/v1/audios/{a['id']}")
    j = (await client.get(f"/api/v1/buttons/{b['id']}")).json()
    assert j["audio_id"] is None
    assert (j["base_button"]["desired_version"], j["base_button"]["desired_audio_id"]) == (5, None)


async def test_hiding_or_deleting_linked_button_unlinks(client, as_user, fcm):
    b = await _linked(client, as_user)
    r = await client.patch(f"/api/v1/buttons/{b['id']}", json={"is_hidden": True})
    bb = r.json()["base_button"]
    assert bb["desired_deleted"] is True and bb["desired_version"] == 2
    assert fcm.keys("ann-phone")[-1] == "button_unlinked"

    # a pending unlink is not re-linked by another sighting
    await client.post(
        "/api/v1/device/events", json=[seen("ABC123", at="2026-09-17T11:00:00Z")], headers=DEVICE
    )
    assert len((await client.get("/api/v1/buttons", params={"include_hidden": "true"})).json()) == 2

    other = await _linked(client, as_user, serial="DEF456")
    assert (await client.delete(f"/api/v1/buttons/{other['id']}")).status_code == 204
    assert fcm.keys("ann-phone").count("button_unlinked") == 2


async def test_explicit_unlink(client, as_user, fcm):
    b = await _linked(client, as_user)
    r = await client.post(f"/api/v1/buttons/{b['id']}/unlink")
    assert r.status_code == 200 and r.json()["base_button"]["desired_deleted"] is True
    assert (await client.post(f"/api/v1/buttons/{b['id']}/unlink")).status_code == 409
    plain = (await client.post("/api/v1/buttons", json={"text": "Play"})).json()
    assert (await client.post(f"/api/v1/buttons/{plain['id']}/unlink")).status_code == 409


async def test_merge_moves_link_and_audio_to_target(client, as_user):
    from tests.test_audios import opus

    source = await _linked(client, as_user)
    a = (
        await client.post(
            "/api/v1/audios", data={"name": "walk"}, files={"file": ("w.ogg", opus())}
        )
    ).json()
    await client.patch(f"/api/v1/buttons/{source['id']}", json={"audio_id": a["id"]})
    target = (await client.post("/api/v1/buttons", json={"text": "Walk"})).json()

    r = await client.post(
        "/api/v1/buttons/merge", json={"source_id": source["id"], "target_id": target["id"]}
    )
    assert r.status_code == 200
    t = r.json()
    assert t["id"] == target["id"] and t["audio_id"] == a["id"]
    assert t["base_button"]["button_serial_number"] == "ABC123"
    assert t["base_button"]["desired_audio_id"] == a["id"]
    assert (await client.get(f"/api/v1/buttons/{source['id']}")).status_code == 404

    # merging into a button that already has a link is refused; so is merging a button with itself
    other = await _linked(client, as_user, serial="DEF456")
    r = await client.post(
        "/api/v1/buttons/merge", json={"source_id": other["id"], "target_id": target["id"]}
    )
    assert r.status_code == 409
    r = await client.post(
        "/api/v1/buttons/merge", json={"source_id": target["id"], "target_id": target["id"]}
    )
    assert r.status_code == 422
