from tests.test_bases import SERIAL
from tests.test_device_events import DEVICE, press

API = "/api/v1"


async def household_of_three(client, as_user):
    """ann (all, default), bob (on_interaction), cid (none); each with a token; base registered."""
    as_user("ann", "ann@example.com", name="Ann")
    await client.put(f"{API}/push-tokens", json={"token": "ann-phone"})
    invites = {}
    for who in ("bob", "cid"):
        r = await client.post(f"{API}/household/invitations", json={"email": f"{who}@x.com"})
        invites[who] = r.json()["id"]
    for who, freq in (("bob", "on_interaction"), ("cid", "none")):
        as_user(who, f"{who}@x.com")
        await client.put(f"{API}/push-tokens", json={"token": f"{who}-phone"})
        await client.post(f"{API}/household/invitations/{invites[who]}/accept")
        await client.put(f"{API}/preferences/push_frequency", json={"value": freq})
    as_user("ann", "ann@example.com")
    await client.post(f"{API}/bases", json={"serial_number": SERIAL, "name": "Kitchen"})


async def test_button_pressed_push_respects_push_frequency(client, as_user, fcm):
    await household_of_three(client, as_user)
    fcm.sent.clear()

    r = await client.post(
        f"{API}/device/events",
        json=[press("AAA111", "2026-09-10T08:00:00Z"), press("BBB222", "2026-09-10T08:00:05Z")],
        headers=DEVICE,
    )
    assert [x["status"] for x in r.json()] == ["created", "created"]
    # first press starts an interaction: ann (all) and bob (on_interaction) hear about it
    assert fcm.keys("ann-phone") == [
        "button_linked",
        "button_pressed",
        "button_linked",
        "button_pressed",
    ]
    assert fcm.keys("bob-phone") == ["button_linked", "button_pressed", "button_linked"]
    assert fcm.keys("cid-phone") == ["button_linked", "button_linked"]
    pressed = [m for m in fcm.sent if m["data"]["key"] == "button_pressed"]
    assert pressed[0]["body"] == "Button AAA111 pressed on Kitchen"
    assert pressed[0]["data"]["text"] == "Button AAA111"
    assert pressed[1]["body"] == "Button BBB222 pressed on Kitchen"

    # a duplicate press sends nothing
    fcm.sent.clear()
    await client.post(
        f"{API}/device/events", json=[press("AAA111", "2026-09-10T08:00:00.400Z")], headers=DEVICE
    )
    assert fcm.sent == []


async def test_base_press_calls_button_webhook_with_retries(client, as_user, webhooks):
    as_user("ann", "ann@example.com", name="Ann")
    await client.post(f"{API}/bases", json={"serial_number": SERIAL, "name": "Kitchen"})
    await client.post(
        f"{API}/device/events", json=[press("AAA111", "2026-09-10T08:00:00Z")], headers=DEVICE
    )
    button = next(
        b for b in (await client.get(f"{API}/buttons")).json() if b["origin"] == "connect"
    )
    url = "https://hooks.example.com/play?x=1"
    await client.patch(f"{API}/buttons/{button['id']}", json={"webhook_url": url})
    webhooks.fail[url] = 1

    await client.post(
        f"{API}/device/events", json=[press("AAA111", "2026-09-10T08:00:03Z")], headers=DEVICE
    )
    assert webhooks.calls == [url, url]  # first attempt failed, second succeeded
    logs = (await client.get(f"{API}/buttons/{button['id']}/webhook-logs")).json()
    assert [(x["url"], x["status_code"]) for x in logs] == [(url, 200), (url, None)]
    assert logs[0]["responded_at"] and logs[1]["responded_at"] is None

    # gives up after three attempts; duplicates and hand-logged presses never call out
    webhooks.calls.clear()
    webhooks.fail[url] = 5
    await client.post(
        f"{API}/device/events", json=[press("AAA111", "2026-09-10T08:00:06Z")], headers=DEVICE
    )
    assert webhooks.calls == [url, url, url]
    webhooks.calls.clear()
    await client.post(
        f"{API}/device/events", json=[press("AAA111", "2026-09-10T08:00:06.500Z")], headers=DEVICE
    )
    await client.post(
        f"{API}/interactions",
        json={"occurred_at": "2026-09-10T09:00:00Z", "button_ids": [button["id"]]},
    )
    assert webhooks.calls == []
    assert len((await client.get(f"{API}/buttons/{button['id']}/webhook-logs")).json()) == 5


def event(type_: str, at: str, payload: dict | None = None, button: str | None = None) -> dict:
    return {
        "serial_number": SERIAL,
        "button_serial_number": button,
        "type": type_,
        "occurred_at": at,
        "payload": payload,
    }


async def base(client):
    return (await client.get(f"{API}/bases")).json()[0]


async def test_battery_power_and_online_events(client, as_user, fcm):
    as_user("ann", "ann@example.com", name="Ann")
    await client.put(f"{API}/push-tokens", json={"token": "ann-phone"})
    await client.post(f"{API}/bases", json={"serial_number": SERIAL, "name": "Kitchen"})
    await client.post(
        f"{API}/device/events",
        json=[event("button_seen", "2026-09-10T07:00:00Z", button="AAA111")],
        headers=DEVICE,
    )
    fcm.sent.clear()
    post = lambda *evs: client.post(f"{API}/device/events", json=list(evs), headers=DEVICE)  # noqa: E731

    r = await post(event("online", "2026-09-10T08:00:00Z"))
    assert r.json() == [{"status": "created"}]
    assert (await base(client))["last_online_at"] == "2026-09-10T08:00:00Z"

    # charging: low level is not alarming
    await post(event("battery", "2026-09-10T08:01:00Z", {"level": 15, "charging": True}))
    b = await base(client)
    assert b["battery_level"] == 15 and b["battery_updated_at"] == "2026-09-10T08:01:00Z"
    assert fcm.keys() == []

    # unplugged and low: one push per user per 24 h
    await post(event("power", "2026-09-10T08:02:00Z", {"charging": False}))
    await post(event("battery", "2026-09-10T08:03:00Z", {"level": 15}))
    await post(event("battery", "2026-09-10T08:04:00Z", {"level": 10}))
    assert fcm.keys("ann-phone") == ["base_battery_low"]
    assert fcm.sent[0]["body"] == "Kitchen is at 15%"
    assert (await base(client))["battery_level"] == 10

    await post(
        event("fully_charged", "2026-09-10T09:00:00Z"),
        event("fully_charged", "2026-09-10T09:30:00Z"),
    )
    assert fcm.keys("ann-phone") == ["base_battery_low", "base_fully_charged"]

    # button batteries live on the link; every event proves the base is online
    await post(event("battery", "2026-09-10T10:00:00Z", {"level": 42}, button="AAA111"))
    b = await base(client)
    assert b["buttons"][0]["battery_level"] == 42 and b["battery_level"] == 10
    assert b["last_online_at"] == "2026-09-10T10:00:00Z"
    assert (await post(event("battery", "2026-09-10T10:01:00Z", {"level": "full"}))).json() == [
        {"status": "invalid"}
    ]


async def test_webhook_url_cannot_point_inside(client, as_user, webhooks):
    as_user("ann", "ann@example.com", name="Ann")
    button = (await client.post(f"{API}/buttons", json={"text": "Play"})).json()
    for bad in (
        "http://169.254.169.254/computeMetadata/v1/",
        "http://metadata.google.internal/",
        "http://localhost:8080/x",
        "http://10.0.0.5/hook",
        "http://[::1]/",
        "ftp://example.com/x",
    ):
        r = await client.patch(f"{API}/buttons/{button['id']}", json={"webhook_url": bad})
        assert r.status_code == 422, bad
    r = await client.patch(
        f"{API}/buttons/{button['id']}", json={"webhook_url": "https://hooks.example.com/ok"}
    )
    assert r.status_code == 200
