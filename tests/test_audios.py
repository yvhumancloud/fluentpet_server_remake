import struct
import zlib


def opus(rate: int = 16000, pad: int = 0) -> bytes:
    """Smallest Ogg page carrying an OpusHead (mapping family 0)."""
    head = b"OpusHead" + bytes([1, 1]) + struct.pack("<HIhB", 312, rate, 0, 0)
    page = b"OggS" + b"\x00\x02" + b"\x00" * 8 + b"\x01\x00\x00\x00" + b"\x00" * 4
    return page + b"\x00" * 4 + bytes([1, len(head)]) + head + b"\x00" * pad


async def test_upload_list_url_delete(client, as_user, s3):
    as_user("u1", "ann@example.com")
    data = opus()
    r = await client.post(
        "/api/v1/audios", data={"name": "walk"}, files={"file": ("walk.ogg", data, "audio/ogg")}
    )
    assert r.status_code == 201
    a = r.json()
    assert (a["name"], a["byte_size"], a["crc32"]) == ("walk", len(data), zlib.crc32(data))
    key = f"audio/h{a['household_id']}/{a['id']}.ogg"
    assert s3.objects == {key: data}

    assert [x["id"] for x in (await client.get("/api/v1/audios")).json()] == [a["id"]]
    r = await client.get(f"/api/v1/audios/{a['id']}/url")
    assert r.status_code == 200 and r.json()["url"] == f"https://r2.test/{key}?expires=900"

    assert (await client.delete(f"/api/v1/audios/{a['id']}")).status_code == 204
    assert s3.objects == {} and (await client.get("/api/v1/audios")).json() == []
    assert (await client.get(f"/api/v1/audios/{a['id']}/url")).status_code == 404


async def test_upload_validation(client, as_user):
    as_user("u1", "ann@example.com")
    post = lambda data: client.post(  # noqa: E731
        "/api/v1/audios", data={"name": "x"}, files={"file": ("x.ogg", data, "audio/ogg")}
    )
    assert (await post(opus(pad=4096))).status_code == 413
    assert (await post(opus(rate=48000))).status_code == 422
    assert (await post(b"RIFF" + b"\0" * 100)).status_code == 422


async def test_delete_audio_detaches_buttons(client, as_user):
    as_user("u1", "ann@example.com")
    a = (
        await client.post(
            "/api/v1/audios", data={"name": "walk"}, files={"file": ("w.ogg", opus())}
        )
    ).json()
    b = (await client.post("/api/v1/buttons", json={"text": "Walk"})).json()
    r = await client.patch(f"/api/v1/buttons/{b['id']}", json={"audio_id": a["id"]})
    assert r.status_code == 200 and r.json()["audio_id"] == a["id"]
    assert (
        await client.patch(f"/api/v1/buttons/{b['id']}", json={"audio_id": 999})
    ).status_code == 422

    await client.delete(f"/api/v1/audios/{a['id']}")
    assert (await client.get(f"/api/v1/buttons/{b['id']}")).json()["audio_id"] is None


async def test_audios_are_household_scoped(client, as_user):
    as_user("u1", "ann@example.com")
    a = (
        await client.post(
            "/api/v1/audios", data={"name": "walk"}, files={"file": ("w.ogg", opus())}
        )
    ).json()
    as_user("u2", "eve@example.com")
    assert (await client.get(f"/api/v1/audios/{a['id']}/url")).status_code == 404
    assert (await client.delete(f"/api/v1/audios/{a['id']}")).status_code == 404
    b = (await client.post("/api/v1/buttons", json={"text": "Walk"})).json()
    assert (
        await client.patch(f"/api/v1/buttons/{b['id']}", json={"audio_id": a["id"]})
    ).status_code == 422
