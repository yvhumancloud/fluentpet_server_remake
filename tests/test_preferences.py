async def test_put_and_get_preferences(client, as_user):
    as_user("u1", "ann@example.com")
    assert (await client.get("/api/v1/preferences")).json() == {}

    r = await client.put("/api/v1/preferences/push_frequency", json={"value": "on_interaction"})
    assert r.status_code == 200 and r.json() == {"key": "push_frequency", "value": "on_interaction"}
    r = await client.put("/api/v1/preferences/feature_flags", json={"value": {"beta": True}})
    assert r.status_code == 200
    r = await client.put("/api/v1/preferences/push_frequency", json={"value": "none"})
    assert r.status_code == 200

    assert (await client.get("/api/v1/preferences")).json() == {
        "push_frequency": "none",
        "feature_flags": {"beta": True},
    }


async def test_preference_key_and_values_validated(client, as_user):
    as_user("u1", "ann@example.com")
    assert (await client.put("/api/v1/preferences/colour", json={"value": 1})).status_code == 404
    r = await client.put("/api/v1/preferences/push_frequency", json={"value": "hourly"})
    assert r.status_code == 422
    r = await client.put("/api/v1/preferences/default_pusher_id", json={"value": 999})
    assert r.status_code == 422  # not a pusher in my household


async def test_hiding_pusher_clears_default_pusher_preference(client, as_user):
    as_user("u1", "ann@example.com")
    rex = (await client.post("/api/v1/pushers", json={"name": "Rex"})).json()
    await client.put("/api/v1/preferences/default_pusher_id", json={"value": rex["id"]})
    assert (await client.get("/api/v1/preferences")).json() == {"default_pusher_id": rex["id"]}

    await client.patch(f"/api/v1/pushers/{rex['id']}", json={"is_hidden": True})
    assert (await client.get("/api/v1/preferences")).json() == {}
