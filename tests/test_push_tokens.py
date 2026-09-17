async def test_register_and_delete_push_token(client, as_user):
    as_user("u1", "ann@example.com")
    r = await client.put("/api/v1/push-tokens", json={"token": "fcm-abc", "platform": "android"})
    assert r.status_code == 204
    assert (await client.put("/api/v1/push-tokens", json={"token": "fcm-abc"})).status_code == 204

    assert (await client.delete("/api/v1/push-tokens/fcm-abc")).status_code == 204
    assert (await client.delete("/api/v1/push-tokens/fcm-abc")).status_code == 404


async def test_token_moves_to_latest_user(client, as_user):
    as_user("u1", "ann@example.com")
    await client.put("/api/v1/push-tokens", json={"token": "shared-device"})
    as_user("u2", "bob@example.com")
    assert (
        await client.put("/api/v1/push-tokens", json={"token": "shared-device"})
    ).status_code == 204

    as_user("u1", "ann@example.com")
    assert (await client.delete("/api/v1/push-tokens/shared-device")).status_code == 404
    as_user("u2", "bob@example.com")
    assert (await client.delete("/api/v1/push-tokens/shared-device")).status_code == 204
