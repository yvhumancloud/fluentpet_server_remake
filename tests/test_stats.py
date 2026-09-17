from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tests.test_interactions import API, ctx_id, setup

PARIS = ZoneInfo("Europe/Paris")


async def test_stats_summary(client, as_user):
    rex, b = await setup(client, as_user)
    await client.patch(f"{API}/me", json={"timezone": "Europe/Paris"})
    today = datetime.now(PARIS).date()
    d1, d2 = today - timedelta(days=3), today - timedelta(days=2)
    await client.patch(
        f"{API}/pushers/{rex['id']}",
        json={"training_started_at": (today - timedelta(days=10)).isoformat()},
    )
    asking = await ctx_id(client, "Asking Question")
    at = lambda d, h, m=0: datetime(d.year, d.month, d.day, h, m, tzinfo=PARIS).isoformat()  # noqa: E731
    log = lambda **body: client.post(f"{API}/interactions", json={"pusher_id": rex["id"], **body})  # noqa: E731
    await log(occurred_at=at(d1, 0, 30), button_ids=[b["Play"], b["Food"]], context_ids=[asking])
    await log(occurred_at=at(d1, 8), button_ids=[b["Play"]], context_ids=[asking])
    await log(occurred_at=at(d2, 8), button_ids=[b["Food"], b["Play"], b["Outside"]])
    await log(
        occurred_at=at(today - timedelta(days=30), 8), button_ids=[b["Outside"]]
    )  # out of range
    ann = next(p for p in (await client.get(f"{API}/pushers")).json() if p["is_human"])
    await client.post(  # someone else's interaction
        f"{API}/interactions",
        json={"pusher_id": ann["id"], "occurred_at": at(d1, 9), "button_ids": [b["Play"]]},
    )

    r = await client.get(
        f"{API}/stats/summary",
        params={"pusher_id": rex["id"], "from": d1.isoformat(), "to": today.isoformat()},
    )
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["pusher"] == {"id": rex["id"], "name": "Rex"}
    assert s["days_since_training_started"] == 10 and s["days_since_first_interaction"] == 30
    assert s["totals"] == {"interactions": 4, "presses": 7, "distinct_buttons": 3}
    rng = s["range"]
    assert rng["from"] == d1.isoformat() and rng["to"] == today.isoformat()
    assert rng["buttons_logged"] == [
        {"text": "Play", "count": 3},
        {"text": "Food", "count": 2},
        {"text": "Outside", "count": 1},
    ]
    assert rng["buttons_created"] == rng["buttons_logged"]  # all buttons were created today
    assert rng["combinations"] == [
        {"text": "Food, Outside, Play", "count": 1},
        {"text": "Food, Play", "count": 1},
    ]
    assert rng["contexts"] == [{"text": "Asking Question", "count": 2}]
    assert rng["per_day"] == [
        {"date": d1.isoformat(), "presses": 3, "interactions": 2},
        {"date": d2.isoformat(), "presses": 3, "interactions": 1},
    ]
    assert rng["per_hour"] == [{"hour": 0, "interactions": 1}, {"hour": 8, "interactions": 2}]

    bad = {"pusher_id": rex["id"], "from": "2026-01-01", "to": "2026-12-31"}
    assert (await client.get(f"{API}/stats/summary", params=bad)).status_code == 422
    bad = {"pusher_id": rex["id"], "from": "2026-02-01", "to": "2026-01-01"}
    assert (await client.get(f"{API}/stats/summary", params=bad)).status_code == 422
    as_user("u2", "bob@example.com")
    ok = {"pusher_id": rex["id"], "from": d1.isoformat(), "to": today.isoformat()}
    assert (await client.get(f"{API}/stats/summary", params=ok)).status_code == 404


async def test_pusher_stats(client, as_user):
    rex, b = await setup(client, as_user)
    await client.patch(f"{API}/me", json={"timezone": "Europe/Paris"})
    asking, inform = await ctx_id(client, "Asking Question"), await ctx_id(client, "Inform")
    log = lambda **body: client.post(f"{API}/interactions", json={"pusher_id": rex["id"], **body})  # noqa: E731
    r = await client.get(f"{API}/pushers/{rex['id']}/stats")
    assert r.status_code == 200 and r.json() == {
        "most_pressed": [],
        "least_pressed": [],
        "top_contexts": [],
        "most_frequent_combination": None,
        "days_since_first_entry": None,
    }
    for t in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon"):
        b[t] = (await client.post(f"{API}/buttons", json={"text": t})).json()["id"]
    day = "2026-09-10T08:00:00Z"
    await log(occurred_at=day, button_ids=[b["Play"], b["Food"]], context_ids=[asking, inform])
    await log(occurred_at=day, button_ids=[b["Food"], b["Play"]], context_ids=[asking])
    await log(occurred_at=day, button_ids=[b["Play"], b["Outside"]])
    for t in ("Alpha", "Beta", "Gamma", "Delta", "Epsilon"):
        await log(occurred_at=day, button_ids=[b[t]])
    await log(occurred_at="2026-09-01T08:00:00Z", button_ids=[b["Play"]])

    s = (await client.get(f"{API}/pushers/{rex['id']}/stats")).json()
    assert s["most_pressed"] == [
        {"text": "Play", "count": 4},
        {"text": "Food", "count": 2},
        {"text": "Alpha", "count": 1},
        {"text": "Beta", "count": 1},
        {"text": "Delta", "count": 1},
    ]
    assert [x["text"] for x in s["least_pressed"]] == ["Alpha", "Beta", "Delta", "Epsilon", "Gamma"]
    assert s["top_contexts"] == [
        {"text": "Asking Question", "count": 2},
        {"text": "Inform", "count": 1},
    ]
    assert s["most_frequent_combination"] == {
        "buttons": [{"id": b["Food"], "text": "Food"}, {"id": b["Play"], "text": "Play"}],
        "count": 2,
    }
    first = datetime(2026, 9, 1, tzinfo=PARIS).date()
    assert s["days_since_first_entry"] == (datetime.now(PARIS).date() - first).days
    assert (await client.get(f"{API}/pushers/999/stats")).status_code == 404
