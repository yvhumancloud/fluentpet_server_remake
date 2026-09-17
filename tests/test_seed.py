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
