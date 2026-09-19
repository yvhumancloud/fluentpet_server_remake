import json
from datetime import UTC, datetime, timedelta

API = "/api/v1"


async def setup_household(client, as_user):
    """Ann (Europe/Paris) with learner Rex and buttons Outside, Play. Returns their ids."""
    as_user("ann", "ann@example.com", name="Ann")
    await client.patch(f"{API}/me", json={"timezone": "Europe/Paris"})
    rex = (await client.post(f"{API}/pushers", json={"name": "Rex", "is_human": False})).json()
    outside = (await client.post(f"{API}/buttons", json={"text": "Outside"})).json()
    play = (await client.post(f"{API}/buttons", json={"text": "Play"})).json()
    return rex["id"], outside["id"], play["id"]


async def test_log_text_returns_a_draft_the_app_can_post(client, as_user, claude):
    rex, outside, play = await setup_household(client, as_user)
    claude.reply(
        {
            "pusher_id": rex,
            "button_ids": [outside, play],
            "context_ids": [],
            "occurred_at": None,
            "note": "we went to the park",
            "unmatched_words": [],
        }
    )

    r = await client.post(f"{API}/ai/log-text", json={"text": "Rex pressed outside then play"})
    assert r.status_code == 200, r.text
    body = r.json()
    draft = body["draft"]
    assert draft["pusher_id"] == rex and draft["button_ids"] == [outside, play]
    assert draft["note"] == "we went to the park" and draft["device_timezone"] == "Europe/Paris"
    assert draft["occurred_at"]  # null from the model means now
    assert body["unmatched_words"] == []

    # the vocabulary reached the model, and the draft is accepted by the real write path
    system = claude.calls[0]["system"]
    assert "Rex" in system and "Outside" in system and "Play" in system
    r = await client.post(f"{API}/interactions", json=draft)
    assert r.status_code == 201 and [p["text"] for p in r.json()["presses"]] == ["Outside", "Play"]


async def test_log_text_drops_ids_outside_the_household(client, as_user, claude):
    as_user("dan", "dan@example.com", name="Dan")
    dan_dog = (await client.post(f"{API}/pushers", json={"name": "Fido", "is_human": False})).json()
    dan_btn = (await client.post(f"{API}/buttons", json={"text": "Treat"})).json()
    rex, outside, play = await setup_household(client, as_user)
    claude.reply(
        {
            "pusher_id": dan_dog["id"],
            "button_ids": [outside, dan_btn["id"], 999999],
            "context_ids": [999999],
            "occurred_at": "2026-09-18T08:00:00+02:00",
            "note": None,
            "unmatched_words": ["treat"],
        }
    )

    r = await client.post(f"{API}/ai/log-text", json={"text": "Rex: outside, treat"})
    assert r.status_code == 200
    draft = r.json()["draft"]
    assert draft["pusher_id"] is None and draft["button_ids"] == [outside]
    assert draft["context_ids"] == [] and draft["occurred_at"] == "2026-09-18T06:00:00Z"
    assert r.json()["unmatched_words"] == ["treat"]


async def test_log_text_naive_time_is_in_the_users_timezone(client, as_user, claude):
    rex, outside, _ = await setup_household(client, as_user)  # Europe/Paris
    claude.reply(
        {
            "pusher_id": rex,
            "button_ids": [outside],
            "context_ids": [],
            "occurred_at": "2026-09-18T08:00",
            "note": None,
            "unmatched_words": [],
        }
    )
    r = await client.post(f"{API}/ai/log-text", json={"text": "Rex outside at 8"})
    assert r.json()["draft"]["occurred_at"] == "2026-09-18T06:00:00Z"


DRAFT = {
    "pusher_id": None,
    "button_ids": [],
    "context_ids": [],
    "occurred_at": None,
    "note": None,
    "unmatched_words": [],
}


async def test_log_text_is_limited_to_50_a_day_per_user(client, as_user, claude):
    await setup_household(client, as_user)
    for _ in range(50):
        claude.reply(DRAFT)
        assert (await client.post(f"{API}/ai/log-text", json={"text": "x"})).status_code == 200
    r = await client.post(f"{API}/ai/log-text", json={"text": "x"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
    assert len(claude.calls) == 50  # the 51st never reached the model

    as_user("bob", "bob@example.com", name="Bob")  # limits are per user
    claude.reply(DRAFT)
    assert (await client.post(f"{API}/ai/log-text", json={"text": "x"})).status_code == 200


async def test_log_text_is_503_when_claude_fails_or_is_not_configured(
    client, as_user, claude, monkeypatch
):
    import anthropic

    await setup_household(client, as_user)
    claude.error = anthropic.APIConnectionError(request=None)
    r = await client.post(f"{API}/ai/log-text", json={"text": "x"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "ai_unavailable"

    claude.error = None
    monkeypatch.setattr("app.services.ai.settings.anthropic_api_key", "")
    r = await client.post(f"{API}/ai/log-text", json={"text": "x"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "ai_unavailable"
    assert len(claude.calls) == 1  # never called without a key


# ---- chat --------------------------------------------------------------------


def day(days_ago: int, hour: int) -> str:
    d = datetime.now(UTC).date() - timedelta(days=days_ago)
    return f"{d.isoformat()}T{hour:02d}:00:00Z"


async def log(client, pusher_id, button_ids, occurred_at):
    body = {"pusher_id": pusher_id, "button_ids": button_ids, "occurred_at": occurred_at}
    assert (await client.post(f"{API}/interactions", json=body)).status_code == 201


async def test_chat_answers_from_the_stats_tool(client, as_user, claude):
    rex, outside, play = await setup_household(client, as_user)
    await log(client, rex, [outside], day(1, 8))
    await log(client, rex, [outside], day(0, 7))
    await log(client, rex, [play], day(0, 9))
    today = datetime.now(UTC).date()
    claude.reply(
        [
            (
                "stats_summary",
                {
                    "pusher_id": rex,
                    "from_date": (today - timedelta(days=7)).isoformat(),
                    "to_date": today.isoformat(),
                },
            ),
            "Rex said Outside twice this week.",
        ]
    )

    r = await client.post(
        f"{API}/ai/chat", json={"messages": [{"role": "user", "content": "what did Rex say?"}]}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"reply": "Rex said Outside twice this week.", "remaining_today": 29}

    stats = json.loads(claude.tool_results[0])
    assert stats["range"]["buttons_logged"][0] == {"text": "Outside", "count": 2}
    call = claude.calls[0]
    assert {t.name for t in call["tools"]} == {"stats_summary", "search_interactions"}
    assert call["messages"] == [{"role": "user", "content": "what did Rex say?"}]
    assert "Rex" in str(call["system"]) and "Outside" in str(call["system"])


async def test_chat_search_tool_lists_interactions_in_the_users_timezone(client, as_user, claude):
    rex, outside, play = await setup_household(client, as_user)  # Europe/Paris
    await log(client, rex, [outside, play], "2026-09-10T06:05:00Z")
    r = await client.post(
        f"{API}/notes", json={"text": "Vet visit", "occurred_at": "2026-09-11T10:00:00Z"}
    )
    assert r.status_code == 201
    claude.reply(
        [
            ("search_interactions", {"pusher_id": rex, "from_date": "2026-09-10"}),
            ("search_interactions", {"from_date": "2026-09-01", "to_date": "2026-09-12"}),
            "done",
        ]
    )
    r = await client.post(
        f"{API}/ai/chat", json={"messages": [{"role": "user", "content": "recent?"}]}
    )
    assert r.status_code == 200
    assert claude.tool_results[0] == "1 matching, showing 1\n2026-09-10 08:05 Rex: OUTSIDE, PLAY"
    assert claude.tool_results[1].splitlines() == [
        "2 matching, showing 2",
        '2026-09-11 12:00 note: "Vet visit"',
        "2026-09-10 08:05 Rex: OUTSIDE, PLAY",
    ]


async def test_chat_tools_cannot_see_other_households(client, as_user, claude):
    as_user("dan", "dan@example.com", name="Dan")
    fido = (await client.post(f"{API}/pushers", json={"name": "Fido", "is_human": False})).json()
    treat = (await client.post(f"{API}/buttons", json={"text": "Treat"})).json()
    await log(client, fido["id"], [treat["id"]], day(0, 8))
    await setup_household(client, as_user)
    claude.reply(
        [
            (
                "stats_summary",
                {"pusher_id": fido["id"], "from_date": "2026-09-01", "to_date": "2026-09-30"},
            ),
            ("search_interactions", {"button_ids": [treat["id"]]}),
            ("search_interactions", {"pusher_id": fido["id"]}),
            "nothing",
        ]
    )
    r = await client.post(f"{API}/ai/chat", json={"messages": [{"role": "user", "content": "?"}]})
    assert r.status_code == 200
    assert claude.tool_results[0] == "error: pusher not found"
    assert claude.tool_results[1] == "0 matching, showing 0"
    assert claude.tool_results[2] == "0 matching, showing 0"


async def test_chat_validates_the_thread_and_limits_30_a_day(client, as_user, claude):
    await setup_household(client, as_user)
    bad = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    r = await client.post(f"{API}/ai/chat", json={"messages": bad})
    assert r.status_code == 422 and "last message" in r.json()["error"]["message"]
    r = await client.post(f"{API}/ai/chat", json={"messages": bad[:1] * 21})
    assert r.status_code == 422
    assert claude.calls == []

    for n in range(30):
        claude.reply(["ok"])
        r = await client.post(f"{API}/ai/chat", json={"messages": bad[:1]})
        assert r.status_code == 200 and r.json()["remaining_today"] == 29 - n
    r = await client.post(f"{API}/ai/chat", json={"messages": bad[:1]})
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"


# ---- weekly digest -------------------------------------------------------------

JOB = {"X-Job-Key": "job-secret"}


async def test_weekly_digest_once_per_household_per_week(client, as_user, claude, fcm, monkeypatch):
    monkeypatch.setattr("app.auth.settings.job_api_key", "job-secret")
    rex, outside, play = await setup_household(client, as_user)
    await client.put(f"{API}/push-tokens", json={"token": "ann-phone"})
    for d in (0, 1, 2):
        await log(client, rex, [outside], day(d, 8))
    await log(client, rex, [play], day(9, 8))  # last week
    as_user("dan", "dan@example.com", name="Dan")  # nothing logged: no digest
    await client.put(f"{API}/push-tokens", json={"token": "dan-phone"})
    fcm.sent.clear()
    claude.reply("Rex said OUTSIDE 3 times this week, mostly around 8 am.")

    r = await client.post(f"{API}/internal/weekly-digest", headers=JOB)
    assert r.status_code == 200 and r.json() == {"sent": 1, "skipped": 0}
    assert fcm.keys("ann-phone") == ["weekly_digest"] and fcm.keys("dan-phone") == []
    assert fcm.sent[0]["body"] == "Rex said OUTSIDE 3 times this week, mostly around 8 am."
    prompt = claude.calls[0]["messages"][0]["content"]
    assert '"Rex"' in prompt and '"text":"Outside","count":3' in prompt.replace(" ", "")
    assert '"text":"Play","count":1' in prompt.replace(" ", "")  # last week, for comparison

    as_user("ann", "ann@example.com", name="Ann")
    r = await client.post(f"{API}/interactions/search", json={"filters": {"notes": "only"}})
    assert [n["text"] for n in r.json()["items"]] == [
        "Weekly digest — Rex said OUTSIDE 3 times this week, mostly around 8 am."
    ]

    r = await client.post(f"{API}/internal/weekly-digest", headers=JOB)
    assert r.json() == {"sent": 0, "skipped": 0} and len(claude.calls) == 1


async def test_weekly_digest_skips_a_household_when_claude_fails(
    client, as_user, claude, fcm, monkeypatch
):
    import anthropic

    monkeypatch.setattr("app.auth.settings.job_api_key", "job-secret")
    rex, outside, _ = await setup_household(client, as_user)
    for d in (0, 1, 2):
        await log(client, rex, [outside], day(d, 8))
    claude.error = anthropic.APIConnectionError(request=None)

    r = await client.post(f"{API}/internal/weekly-digest", headers=JOB)
    assert r.status_code == 200 and r.json() == {"sent": 0, "skipped": 1}
    assert fcm.sent == []
    r = await client.post(f"{API}/interactions/search", json={"filters": {"notes": "only"}})
    assert r.json()["items"] == []

    claude.error = None  # next run retries it
    claude.reply("Rex said OUTSIDE 3 times.")
    r = await client.post(f"{API}/internal/weekly-digest", headers=JOB)
    assert r.json() == {"sent": 1, "skipped": 0}
