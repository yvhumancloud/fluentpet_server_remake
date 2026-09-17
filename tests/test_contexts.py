async def test_list_contexts_by_kind(client, as_user):
    as_user("u1", "ann@example.com")
    teacher = (await client.get("/api/v1/contexts", params={"kind": "teacher"})).json()
    learner = (await client.get("/api/v1/contexts", params={"kind": "learner"})).json()
    custom = (await client.get("/api/v1/contexts", params={"kind": "custom"})).json()
    assert "Modeled" in [c["text"] for c in teacher]
    assert "Nobody Home" not in [c["text"] for c in teacher]
    assert "Modeled" not in [c["text"] for c in learner]
    assert "Nobody Home" in [c["text"] for c in learner]
    assert custom == []
    assert (await client.get("/api/v1/contexts", params={"kind": "alien"})).status_code == 422


async def test_create_custom_context(client, as_user):
    as_user("u1", "ann@example.com")
    r = await client.post("/api/v1/contexts", json={"text": "  Bedtime "})
    assert r.status_code == 201
    c = r.json()
    assert c["text"] == "Bedtime" and c["applies_to"] == "both" and c["household_id"]
    assert (await client.post("/api/v1/contexts", json={"text": "bedtime"})).status_code == 409
    assert (await client.post("/api/v1/contexts", json={"text": "Modeled"})).status_code == 409
    assert [
        x["id"] for x in (await client.get("/api/v1/contexts", params={"kind": "custom"})).json()
    ] == [c["id"]]
    as_user("u2", "bob@example.com")
    assert (await client.get("/api/v1/contexts", params={"kind": "custom"})).json() == []
