async def test_learner_types_seeded(client, as_user):
    as_user("u1", "ann@example.com")
    r = await client.get("/api/v1/learner-types")
    assert r.status_code == 200
    assert [t["name"] for t in r.json()] == ["dog", "cat", "other"]


async def test_create_and_list_pushers(client, as_user):
    as_user("u1", "ann@example.com", name="Ann")
    types = (await client.get("/api/v1/learner-types")).json()
    dog = next(t["id"] for t in types if t["name"] == "dog")
    r = await client.post(
        "/api/v1/pushers",
        json={"name": "Rex", "learner_type_id": dog, "birth_date": "2020-05-01", "sex": "male"},
    )
    assert r.status_code == 201
    rex = r.json()
    assert (rex["name"], rex["is_human"], rex["learner_type_id"], rex["birth_date"]) == (
        "Rex",
        False,
        dog,
        "2020-05-01",
    )
    names = [p["name"] for p in (await client.get("/api/v1/pushers")).json()]
    assert sorted(names) == ["Ann", "Rex"]

    one = await client.get(f"/api/v1/pushers/{rex['id']}")
    assert one.status_code == 200 and one.json()["name"] == "Rex"


async def test_duplicate_name_conflicts_unless_hidden(client, as_user):
    as_user("u1", "ann@example.com")
    rex = (await client.post("/api/v1/pushers", json={"name": "Rex"})).json()
    assert (await client.post("/api/v1/pushers", json={"name": "rex"})).status_code == 409

    await client.patch(f"/api/v1/pushers/{rex['id']}", json={"is_hidden": True})
    assert [p["id"] for p in (await client.get("/api/v1/pushers")).json()] != [rex["id"]]
    listed = (await client.get("/api/v1/pushers", params={"include_hidden": "true"})).json()
    assert any(p["id"] == rex["id"] and p["is_hidden"] for p in listed)

    r = await client.post("/api/v1/pushers", json={"name": "REX"})
    assert r.status_code == 200 and r.json()["id"] == rex["id"] and r.json()["is_hidden"] is False


async def test_patch_and_delete_pusher(client, as_user):
    as_user("u1", "ann@example.com")
    rex = (await client.post("/api/v1/pushers", json={"name": "Rex"})).json()
    r = await client.patch(f"/api/v1/pushers/{rex['id']}", json={"name": "Rexy", "country": "IN"})
    assert r.status_code == 200 and r.json()["name"] == "Rexy" and r.json()["country"] == "IN"

    assert (await client.delete(f"/api/v1/pushers/{rex['id']}")).status_code == 204
    assert (await client.get(f"/api/v1/pushers/{rex['id']}")).status_code == 404


async def test_pushers_are_household_scoped(client, as_user):
    as_user("u1", "ann@example.com")
    rex = (await client.post("/api/v1/pushers", json={"name": "Rex"})).json()
    as_user("u2", "eve@example.com")
    assert (await client.get(f"/api/v1/pushers/{rex['id']}")).status_code == 404
    assert (
        await client.patch(f"/api/v1/pushers/{rex['id']}", json={"name": "x"})
    ).status_code == 404
    assert (await client.delete(f"/api/v1/pushers/{rex['id']}")).status_code == 404


async def test_rename_to_existing_name_conflicts(client, as_user):
    as_user("u1", "ann@example.com", name="Ann")
    rex = (await client.post("/api/v1/pushers", json={"name": "Rex"})).json()
    r = await client.patch(f"/api/v1/pushers/{rex['id']}", json={"name": "ann"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"


PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


async def test_avatar_upload_stored_and_presigned(client, as_user, s3):
    as_user("u1", "ann@example.com")
    rex = (await client.post("/api/v1/pushers", json={"name": "Rex"})).json()
    assert rex["avatar_url"] is None

    r = await client.put(
        f"/api/v1/pushers/{rex['id']}/avatar", files={"file": ("me.png", PNG, "image/png")}
    )
    assert r.status_code == 200
    url = r.json()["avatar_url"]
    assert url.startswith("https://r2.test/avatars/") and "expires=900" in url
    assert list(s3.objects.values()) == [PNG]

    listed = (await client.get("/api/v1/pushers")).json()
    assert next(p for p in listed if p["id"] == rex["id"])["avatar_url"] == url


async def test_avatar_rejects_non_image_and_oversize(client, as_user):
    as_user("u1", "ann@example.com")
    rex = (await client.post("/api/v1/pushers", json={"name": "Rex"})).json()
    url = f"/api/v1/pushers/{rex['id']}/avatar"
    r = await client.put(url, files={"file": ("x.png", b"not an image", "image/png")})
    assert r.status_code == 422
    big = b"\xff\xd8\xff" + b"\0" * (2 * 1024 * 1024)
    r = await client.put(url, files={"file": ("x.jpg", big, "image/jpeg")})
    assert r.status_code == 413


async def test_delete_pusher_removes_avatar_object(client, as_user, s3):
    as_user("u1", "ann@example.com")
    rex = (await client.post("/api/v1/pushers", json={"name": "Rex"})).json()
    await client.put(f"/api/v1/pushers/{rex['id']}/avatar", files={"file": ("a.png", PNG)})
    assert len(s3.objects) == 1
    await client.delete(f"/api/v1/pushers/{rex['id']}")
    assert s3.objects == {}
