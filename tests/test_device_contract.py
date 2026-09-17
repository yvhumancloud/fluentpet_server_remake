"""What a device script must do, in order. Read this before writing one."""

from tests.test_audios import opus
from tests.test_bases import SERIAL
from tests.test_device_events import DEVICE

API = "/api/v1"
D = f"{API}/device"


async def test_device_script_contract(client, as_user, fcm):
    # 0. The owner registers the base and prepares a button with audio in the app.
    as_user("ann", "ann@example.com", name="Ann")
    await client.post(f"{API}/bases", json={"serial_number": SERIAL, "name": "Kitchen"})
    audio = (
        await client.post(
            f"{API}/audios", data={"name": "walk"}, files={"file": ("w.ogg", opus(), "audio/ogg")}
        )
    ).json()

    # 1. Boot: every request carries X-Device-Key; anything else is 401.
    assert (
        await client.put(f"{D}/bases/{SERIAL}/state", json={"reported_state": {}})
    ).status_code == 401
    r = await client.put(
        f"{D}/bases/{SERIAL}/state",
        json={
            "reported_state": {"fw": "2.1.0", "ip": "10.0.0.7"},
            "fw_version": "2.1.0",
            "battery_level": 90,
        },
        headers=DEVICE,
    )
    assert r.status_code == 204

    # 2. Report the buttons that are physically present. Unknown serials become buttons.
    batch = [
        {
            "serial_number": SERIAL,
            "button_serial_number": "AAA111",
            "type": "button_seen",
            "occurred_at": 1789000000,
        },
        {
            "serial_number": SERIAL,
            "button_serial_number": "BBB222",
            "type": "button_seen",
            "occurred_at": 1789000000,
        },
    ]
    r = await client.post(f"{D}/events", json=batch, headers=DEVICE)
    assert [x["status"] for x in r.json()] == ["created", "created"]
    assert [
        x["status"] for x in (await client.post(f"{D}/events", json=batch, headers=DEVICE)).json()
    ] == [
        "duplicate",
        "duplicate",
    ]  # re-delivery after a crash is safe

    # 3. Poll desired state; apply; ack the version you applied.
    pending = (
        await client.get(f"{D}/desired", params={"serial_number": SERIAL}, headers=DEVICE)
    ).json()
    assert {p["button_serial_number"] for p in pending} == {"AAA111", "BBB222"}
    aaa = next(
        x for x in (await client.get(f"{API}/buttons")).json() if x["text"] == "Button AAA111"
    )
    await client.patch(
        f"{API}/buttons/{aaa['id']}", json={"audio_id": audio["id"]}
    )  # the owner, meanwhile
    pending = (
        await client.get(f"{D}/desired", params={"serial_number": SERIAL}, headers=DEVICE)
    ).json()
    wanted = next(p for p in pending if p["button_serial_number"] == "AAA111")
    assert wanted["desired_version"] == 2 and wanted["audio"]["crc32"] == audio["crc32"]
    assert wanted["audio"]["url"].startswith("https://")  # download, verify CRC, flash
    acks = [
        {"base_button_id": p["base_button_id"], "applied_version": p["desired_version"]}
        for p in pending
    ]
    assert (await client.post(f"{D}/desired/ack", json=acks, headers=DEVICE)).status_code == 204
    assert (
        await client.get(f"{D}/desired", params={"serial_number": SERIAL}, headers=DEVICE)
    ).json() == []

    # 4. Stream events as they happen. Timestamps: ISO 8601, epoch seconds, or epoch millis.
    events = [
        {
            "serial_number": SERIAL,
            "button_serial_number": "AAA111",
            "type": "press",
            "occurred_at": 1789000100,
        },
        {
            "serial_number": SERIAL,
            "button_serial_number": "BBB222",
            "type": "press",
            "occurred_at": 1789000103500,
        },
        {
            "serial_number": SERIAL,
            "button_serial_number": "AAA111",
            "type": "battery",
            "occurred_at": 1789000104,
            "payload": {"level": 60},
        },
        {
            "serial_number": SERIAL,
            "type": "battery",
            "occurred_at": 1789000105,
            "payload": {"level": 18, "charging": False},
        },
        {
            "serial_number": SERIAL,
            "type": "power",
            "occurred_at": 1789000106,
            "payload": {"charging": True},
        },
        {"serial_number": SERIAL, "type": "fully_charged", "occurred_at": 1789000200},
        {"serial_number": SERIAL, "type": "online", "occurred_at": 1789000300},
        {"serial_number": "NOPE00000000", "type": "online", "occurred_at": 1789000300},
        {
            "serial_number": SERIAL,
            "button_serial_number": "000",
            "type": "press",
            "occurred_at": 1789000301,
        },
    ]
    r = await client.post(f"{D}/events", json=events, headers=DEVICE)
    assert [x["status"] for x in r.json()] == ["created"] * 7 + ["unknown_base", "invalid"]
    feed = (await client.post(f"{API}/interactions/search", json={})).json()["items"]
    assert [p["text"] for p in feed[0]["presses"]] == ["Button AAA111", "Button BBB222"]
    assert feed[0]["duration_seconds"] == 3.5

    # 5. Audio can also be fetched on demand (e.g. after a factory reset).
    r = await client.post(
        f"{D}/audio-url", json={"serial_number": SERIAL, "audio_id": audio["id"]}, headers=DEVICE
    )
    assert r.status_code == 200 and r.json()["crc32"] == audio["crc32"]

    # 6. The owner sees what happened.
    base = (await client.get(f"{API}/bases")).json()[0]
    assert (base["fw_version"], base["battery_level"], base["reported_state"]["charging"]) == (
        "2.1.0",
        18,
        True,
    )
    assert fcm.sent == []  # no push tokens registered: nothing sent
