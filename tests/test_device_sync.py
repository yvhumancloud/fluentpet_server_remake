from datetime import UTC, datetime, timedelta

from tests.test_bases import SERIAL
from tests.test_device_events import DEVICE

API = "/api/v1"


async def test_state_report(client, as_user):
    as_user("ann", "ann@example.com", name="Ann")
    await client.post(f"{API}/bases", json={"serial_number": SERIAL, "name": "Kitchen"})
    r = await client.put(
        f"{API}/device/bases/{SERIAL.lower()}/state",
        json={
            "reported_state": {"wifi": "home", "charging": True},
            "fw_version": "2.1.0",
            "battery_level": 77,
        },
        headers=DEVICE,
    )
    assert r.status_code == 204, r.text
    b = (await client.get(f"{API}/bases")).json()[0]
    assert b["reported_state"] == {"wifi": "home", "charging": True}
    assert b["fw_version"] == "2.1.0" and b["battery_level"] == 77
    assert datetime.now(UTC) - datetime.fromisoformat(b["last_online_at"]) < timedelta(seconds=5)

    r = await client.put(
        f"{API}/device/bases/{SERIAL}/state",
        json={"reported_state": {"wifi": "cafe"}},
        headers=DEVICE,
    )
    assert r.status_code == 204
    b = (await client.get(f"{API}/bases")).json()[0]
    assert b["reported_state"] == {"wifi": "cafe"} and b["fw_version"] == "2.1.0"

    assert (
        await client.put(
            f"{API}/device/bases/ZZZ999999999/state", json={"reported_state": {}}, headers=DEVICE
        )
    ).status_code == 404
    assert (
        await client.put(f"{API}/device/bases/{SERIAL}/state", json={"reported_state": {}})
    ).status_code == 401
    assert (
        await client.put(
            f"{API}/device/bases/{SERIAL}/state",
            json={"reported_state": {}, "battery_level": 101},
            headers=DEVICE,
        )
    ).status_code == 422


async def linked_pair(client, as_user):
    """Base with buttons AAA111 and BBB222 linked; returns {serial: button}."""
    from tests.test_device_events import seen

    as_user("ann", "ann@example.com", name="Ann")
    await client.post(f"{API}/bases", json={"serial_number": SERIAL, "name": "Kitchen"})
    await client.post(f"{API}/device/events", json=[seen("AAA111"), seen("BBB222")], headers=DEVICE)
    buttons = (await client.get(f"{API}/buttons")).json()
    return {b["base_button"]["button_serial_number"]: b for b in buttons if b["base_button"]}


async def test_desired_state_and_ack(client, as_user):
    from tests.test_audios import opus
    from tests.test_device_events import seen

    links = await linked_pair(client, as_user)
    a, b = links["AAA111"], links["BBB222"]
    desired = lambda **params: client.get(f"{API}/device/desired", params=params, headers=DEVICE)  # noqa: E731
    # fresh links are v1/applied 0: the base must be told about them
    pending = (await desired(serial_number=SERIAL)).json()
    assert [
        (p["button_serial_number"], p["desired_version"], p["applied_version"], p["audio"])
        for p in pending
    ] == [
        ("AAA111", 1, 0, None),
        ("BBB222", 1, 0, None),
    ]
    r = await client.post(
        f"{API}/device/desired/ack",
        json=[{"base_button_id": p["base_button_id"], "applied_version": 1} for p in pending],
        headers=DEVICE,
    )
    assert r.status_code == 204
    assert (await desired(serial_number=SERIAL)).json() == []

    audio = (
        await client.post(
            f"{API}/audios", data={"name": "walk"}, files={"file": ("w.ogg", opus(), "audio/ogg")}
        )
    ).json()
    await client.patch(f"{API}/buttons/{a['id']}", json={"audio_id": audio["id"]})
    await client.post(f"{API}/buttons/{b['id']}/unlink")
    pending = (await desired(serial_number=SERIAL)).json()
    assert [
        (p["button_serial_number"], p["desired_version"], p["desired_deleted"]) for p in pending
    ] == [
        ("AAA111", 2, False),
        ("BBB222", 2, True),
    ]
    assert pending[0]["audio"] == {
        "id": audio["id"],
        "url": f"https://r2.test/audio/h{audio['household_id']}/{audio['id']}.ogg?expires=900",
        "crc32": audio["crc32"],
    }
    assert (
        pending[0]["serial_number"] == SERIAL
        and pending[0]["base_button_id"] == a["base_button"]["id"]
    )
    assert (await desired()).status_code == 422  # a base only ever asks about itself

    # stale or unknown acks are ignored; the unlink row disappears once applied
    r = await client.post(
        f"{API}/device/desired/ack",
        json=[
            {"base_button_id": pending[0]["base_button_id"], "applied_version": 1},
            {"base_button_id": pending[1]["base_button_id"], "applied_version": 2},
            {"base_button_id": 999, "applied_version": 9},
        ],
        headers=DEVICE,
    )
    assert r.status_code == 204
    pending = (await desired(serial_number=SERIAL)).json()
    assert [(p["button_serial_number"], p["applied_version"]) for p in pending] == [("AAA111", 1)]
    got = (await client.get(f"{API}/buttons/{b['id']}")).json()
    assert got["base_button"] is None
    base = (await client.get(f"{API}/bases")).json()[0]
    assert [x["button_serial_number"] for x in base["buttons"]] == ["AAA111"]

    # the freed serial is a new connect button (PRD §7); the unlinked one stays as it was
    await client.post(
        f"{API}/device/events", json=[seen("BBB222", "2026-09-17T11:00:00Z")], headers=DEVICE
    )
    buttons = (await client.get(f"{API}/buttons")).json()
    fresh = next(
        x
        for x in buttons
        if x["base_button"] and x["base_button"]["button_serial_number"] == "BBB222"
    )
    assert fresh["id"] != b["id"] and fresh["text"] == "Button BBB222 (2)"
    assert next(x for x in buttons if x["id"] == b["id"])["base_button"] is None


async def test_audio_url_for_device(client, as_user):
    from tests.test_audios import opus

    as_user("ann", "ann@example.com", name="Ann")
    await client.post(f"{API}/bases", json={"serial_number": SERIAL, "name": "Kitchen"})
    audio = (
        await client.post(
            f"{API}/audios", data={"name": "walk"}, files={"file": ("w.ogg", opus(), "audio/ogg")}
        )
    ).json()
    r = await client.post(
        f"{API}/device/audio-url",
        json={"serial_number": SERIAL, "audio_id": audio["id"]},
        headers=DEVICE,
    )
    assert r.status_code == 200, r.text
    assert r.json() == {
        "url": f"https://r2.test/audio/h{audio['household_id']}/{audio['id']}.ogg?expires=900",
        "crc32": audio["crc32"],
    }
    r = await client.post(
        f"{API}/device/audio-url", json={"serial_number": SERIAL, "audio_id": 999}, headers=DEVICE
    )
    assert r.status_code == 403
    r = await client.post(
        f"{API}/device/audio-url",
        json={"serial_number": "ZZZ999999999", "audio_id": audio["id"]},
        headers=DEVICE,
    )
    assert r.status_code == 404

    as_user("bob", "bob@x.com")
    other = (
        await client.post(
            f"{API}/audios", data={"name": "no"}, files={"file": ("n.ogg", opus(), "audio/ogg")}
        )
    ).json()
    r = await client.post(
        f"{API}/device/audio-url",
        json={"serial_number": SERIAL, "audio_id": other["id"]},
        headers=DEVICE,
    )
    assert r.status_code == 403
