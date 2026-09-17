async def test_note_lifecycle(client, as_user):
    as_user("u1", "ann@example.com")
    r = await client.post(
        "/api/v1/notes",
        json={
            "text": "Vet visit",
            "occurred_at": "2026-09-10T08:00:00Z",
            "device_timezone": "Europe/Paris",
        },
    )
    assert r.status_code == 201
    n = r.json()
    assert n["type"] == "note" and n["text"] == "Vet visit" and n["is_favourite"] is False
    assert n["occurred_at"] == "2026-09-10T08:00:00Z"

    r = await client.patch(f"/api/v1/notes/{n['id']}", json={"is_favourite": True, "text": "Vet"})
    assert r.status_code == 200 and r.json()["is_favourite"] is True and r.json()["text"] == "Vet"

    as_user("u2", "bob@example.com")
    assert (await client.patch(f"/api/v1/notes/{n['id']}", json={"text": "x"})).status_code == 404
    assert (await client.delete(f"/api/v1/notes/{n['id']}")).status_code == 404

    as_user("u1", "ann@example.com")
    assert (await client.delete(f"/api/v1/notes/{n['id']}")).status_code == 204
    assert (await client.delete(f"/api/v1/notes/{n['id']}")).status_code == 404
    assert (
        await client.post(
            "/api/v1/notes", json={"text": " ", "occurred_at": "2026-09-10T08:00:00Z"}
        )
    ).status_code == 422
