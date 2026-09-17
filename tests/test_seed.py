import pytest

from scripts import seed


async def test_seed_is_rerunnable_and_visible_through_the_api(client, as_user):
    first = await seed.run()
    second = await seed.run()  # idempotent: wipes and recreates
    assert second["interactions"] == first["interactions"] > 30
    as_user("dev-ann", "ann@example.com", name="Ann")
    me = (await client.get("/api/v1/me")).json()
    assert me["household"]["id"] == second["household_id"] and me["is_household_admin"]
    names = {p["name"] for p in me["pushers"]}
    assert names == {"Ann", "Bob", "Rex", "Tom"}
    buttons = (await client.get("/api/v1/buttons")).json()
    assert {b["text"] for b in buttons} >= {"Play", "Outside", "Food", "Love you ❤️", "inaudible"}
    assert next(b for b in buttons if b["text"] == "Play")["base_button"]["button_serial_number"]
    base = (await client.get("/api/v1/bases")).json()[0]
    assert base["serial_number"] == seed.SERIAL and len(base["buttons"]) == 3
    feed = (await client.post("/api/v1/interactions/search", json={})).json()
    assert feed["total"] > 30 and {x["type"] for x in feed["items"]} == {"interaction", "note"}
    assert feed["counts"]["modeling"] > 0 and feed["counts"]["unassigned"] > 0
    hh = (await client.get("/api/v1/household")).json()
    assert [i["email"] for i in hh["invitations"]] == ["carol@example.com"]
    as_user("dev-dan", "dan@example.com", name="Dan")
    assert (await client.post("/api/v1/interactions/search", json={})).json()["total"] == 0


async def test_seed_scales_and_can_target_a_real_user(client, as_user):
    as_user("real-uid", "real@example.com", name="Real")
    assert (await client.get("/api/v1/me")).status_code == 200  # first sign-in provisions
    info = await seed.run(days=30, per_day=6, email="real@example.com")
    assert info["interactions"] > 120
    me = (await client.get("/api/v1/me")).json()
    assert me["household"]["id"] == info["household_id"] and me["is_household_admin"]
    assert {p["name"] for p in me["pushers"]} == {"Real", "Bob", "Rex", "Tom"}
    feed = (await client.post("/api/v1/interactions/search", json={})).json()
    assert feed["total"] > 120
    words = {b["text"] for x in feed["items"] if x["type"] == "interaction" for b in x["presses"]}
    assert (
        len(words) > 3
    )  # app-origin interactions use the whole vocabulary, not just linked buttons
    with pytest.raises(SystemExit):
        await seed.run(email="nobody@example.com")


async def test_seed_by_uid_provisions_a_user_who_has_not_signed_in_yet(client, as_user):
    uid = "tgsoCPXFrAMKcIwEoWOk1cfN4jR2"
    with pytest.raises(SystemExit):  # unknown uid, no email to create them with
        await seed.run(days=3, uid=uid)
    info = await seed.run(days=3, uid=uid, email="tester@example.com", name="Tester")
    as_user(uid, "tester@example.com", name="Tester")  # first real sign-in finds the seeded user
    me = (await client.get("/api/v1/me")).json()
    assert me["household"]["id"] == info["household_id"] and me["email"] == "tester@example.com"
    assert {p["name"] for p in me["pushers"]} == {"Tester", "Bob", "Rex", "Tom"}
    again = await seed.run(days=3, uid=uid)  # exists now: no email needed, rebuilds in place
    assert (await client.get("/api/v1/me")).json()["household"]["id"] == again["household_id"]
