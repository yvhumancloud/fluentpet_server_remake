from tests.test_interactions import API, ctx_id, setup


async def log(client, at, **fields):
    r = await client.post(f"{API}/interactions", json={"occurred_at": at, **fields})
    assert r.status_code == 201, r.text
    return r.json()


async def search(client, **body):
    r = await client.post(f"{API}/interactions/search", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_search_pages_sorts_mixes_notes_and_counts(client, as_user):
    rex, b = await setup(client, as_user)
    ann = next(p for p in (await client.get(f"{API}/pushers")).json() if p["is_human"])
    i1 = await log(client, "2026-09-10T08:00:00Z", pusher_id=rex["id"], button_ids=[b["Play"]])
    i2 = await log(client, "2026-09-10T09:00:00Z", pusher_id=ann["id"], button_ids=[b["Food"]])
    i3 = await log(client, "2026-09-10T10:00:00Z", button_ids=[b["Outside"]])
    n1 = (
        await client.post(
            f"{API}/notes", json={"text": "nap", "occurred_at": "2026-09-10T09:30:00Z"}
        )
    ).json()
    deleted = await log(client, "2026-09-10T11:00:00Z", button_ids=[b["Play"]])
    await client.delete(f"{API}/interactions/{deleted['id']}")

    s = await search(client)
    assert (s["total"], s["page"], s["per_page"]) == (4, 1, 45)
    assert [(x["type"], x["id"]) for x in s["items"]] == [
        ("interaction", i3["id"]),
        ("note", n1["id"]),
        ("interaction", i2["id"]),
        ("interaction", i1["id"]),
    ]
    assert s["counts"] == {"communication": 1, "modeling": 1, "unassigned": 1}
    assert s["items"][0]["presses"][0]["text"] == "Outside" and s["items"][1]["text"] == "nap"

    s = await search(client, sort="occurred_at_asc", page=2, per_page=3)
    assert [x["id"] for x in s["items"]] == [i3["id"]] and s["total"] == 4

    s = await search(client, sort="created_at_desc", per_page=2)
    assert [(x["type"], x["id"]) for x in s["items"]] == [
        ("note", n1["id"]),
        ("interaction", i3["id"]),
    ]

    assert (await client.post(f"{API}/interactions/search", json={"page": 0})).status_code == 422

    as_user("u2", "bob@example.com")
    assert (await search(client))["total"] == 0


async def ids(client, **body):
    return [x["id"] for x in (await search(client, **body))["items"]]


async def test_search_tabs_and_entity_filters(client, as_user):
    from tests.test_bases import SERIAL
    from tests.test_device_events import DEVICE, seen

    rex, b = await setup(client, as_user)
    ann = next(p for p in (await client.get(f"{API}/pushers")).json() if p["is_human"])
    asking = await ctx_id(client, "Asking Question")
    inform = await ctx_id(client, "Inform")
    base = (
        await client.post(f"{API}/bases", json={"serial_number": SERIAL, "name": "Hall"})
    ).json()
    await client.post(f"{API}/device/events", json=[seen("ABC123")], headers=DEVICE)
    linked = next(
        x for x in (await client.get(f"{API}/buttons")).json() if x["origin"] == "connect"
    )

    i1 = await log(
        client,
        "2026-09-10T08:00:00Z",
        pusher_id=rex["id"],
        button_ids=[b["Play"]],
        context_ids=[asking],
    )
    i2 = await log(
        client,
        "2026-09-10T09:00:00Z",
        pusher_id=rex["id"],
        button_ids=[b["Play"], b["Food"]],
        context_ids=[asking, inform],
    )
    i3 = await log(client, "2026-09-10T10:00:00Z", pusher_id=ann["id"], button_ids=[b["Food"]])
    i4 = await log(client, "2026-09-10T11:00:00Z", button_ids=[linked["id"]])
    await client.post(f"{API}/notes", json={"text": "nap", "occurred_at": "2026-09-10T12:00:00Z"})

    assert await ids(client, tab="assigned") == [i3["id"], i2["id"], i1["id"]]
    assert await ids(client, tab="unassigned") == [i4["id"]]
    assert (await search(client, tab="assigned"))["counts"] == {
        "communication": 2,
        "modeling": 1,
        "unassigned": 0,
    }

    f = lambda **kw: {"filters": kw}  # noqa: E731
    assert await ids(client, **f(pusher_ids=[rex["id"]])) == [i2["id"], i1["id"]]
    assert await ids(client, **f(pusher_ids=[rex["id"], ann["id"]])) == [
        i3["id"],
        i2["id"],
        i1["id"],
    ]
    assert await ids(client, **f(button_ids=[b["Play"], b["Food"]])) == [
        i3["id"],
        i2["id"],
        i1["id"],
    ]
    assert await ids(client, **f(button_ids=[b["Play"], b["Food"]], match="all")) == [i2["id"]]
    assert await ids(client, **f(context_ids=[asking, inform])) == [i2["id"], i1["id"]]
    assert await ids(client, **f(context_ids=[asking, inform], match="all")) == [i2["id"]]
    assert await ids(client, **f(base_ids=[base["id"]])) == [i4["id"]]
    # an entity filter never matches a note
    kinds = lambda s: [x["type"] for x in s["items"]]  # noqa: E731
    assert "note" not in kinds(await search(client, **f(pusher_ids=[rex["id"]])))
    assert "note" in kinds(await search(client))


async def test_search_text_date_note_and_press_filters(client, as_user):
    rex, b = await setup(client, as_user)
    single = await log(client, "2026-09-10T08:00:00Z", pusher_id=rex["id"], button_ids=[b["Play"]])
    multi = await log(
        client, "2026-09-11T08:00:00Z", button_ids=[b["Play"], b["Food"]], note="Wants Dinner"
    )
    fav = await log(client, "2026-09-12T08:00:00Z", button_ids=[b["Outside"]], is_favourite=True)
    hidden = await log(client, "2026-09-13T08:00:00Z", button_ids=[b["Outside"]])
    await client.patch(f"{API}/interactions/{hidden['id']}", json={"is_hidden": True})
    note = (
        await client.post(
            f"{API}/notes", json={"text": "dinner was late", "occurred_at": "2026-09-11T20:00:00Z"}
        )
    ).json()
    f = lambda **kw: {"filters": kw}  # noqa: E731
    key = lambda s: [(x["type"], x["id"]) for x in s["items"]]  # noqa: E731
    I, N = "interaction", "note"  # noqa: E741

    assert key(await search(client)) == [
        (I, fav["id"]),
        (N, note["id"]),
        (I, multi["id"]),
        (I, single["id"]),
    ]
    assert key(await search(client, **f(include_hidden=True)))[0] == (I, hidden["id"])

    assert key(await search(client, **f(text="DINNER"))) == [(N, note["id"]), (I, multi["id"])]
    assert key(
        await search(client, **f(**{"from": "2026-09-11T00:00:00Z", "to": "2026-09-11T23:59:59Z"}))
    ) == [
        (N, note["id"]),
        (I, multi["id"]),
    ]
    assert key(await search(client, **f(to="2026-09-10T23:59:59Z"))) == [(I, single["id"])]

    assert key(await search(client, **f(notes="only"))) == [(N, note["id"])]
    assert key(await search(client, **f(notes="exclude"))) == [
        (I, fav["id"]),
        (I, multi["id"]),
        (I, single["id"]),
    ]
    assert key(await search(client, **f(with_note="only"))) == [(I, multi["id"])]
    assert key(await search(client, **f(with_note="exclude"))) == [
        (I, fav["id"]),
        (N, note["id"]),
        (I, single["id"]),
    ]
    assert key(await search(client, **f(favourites="only"))) == [(I, fav["id"])]
    assert key(await search(client, **f(favourites="exclude"))) == [
        (N, note["id"]),
        (I, multi["id"]),
        (I, single["id"]),
    ]
    assert key(await search(client, **f(presses="single"))) == [(I, fav["id"]), (I, single["id"])]
    assert key(await search(client, **f(presses="multiple"))) == [(I, multi["id"])]
    assert (
        await client.post(f"{API}/interactions/search", json=f(presses="lots"))
    ).status_code == 422
