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
