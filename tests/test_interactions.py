API = "/api/v1"


async def setup(client, as_user, uid="u1", email="ann@example.com"):
    """Household with learner Rex, buttons Play/Outside/Food; returns (rex, {text: id})."""
    as_user(uid, email, name="Ann")
    rex = (await client.post(f"{API}/pushers", json={"name": "Rex"})).json()
    buttons = {}
    for t in ("Play", "Outside", "Food"):
        buttons[t] = (await client.post(f"{API}/buttons", json={"text": t})).json()["id"]
    return rex, buttons


async def ctx_id(client, text):
    for kind in ("learner", "teacher", "custom"):
        for c in (await client.get(f"{API}/contexts", params={"kind": kind})).json():
            if c["text"] == text:
                return c["id"]
    raise KeyError(text)


async def test_create_and_get_interaction(client, as_user):
    rex, b = await setup(client, as_user)
    asking = await ctx_id(client, "Asking Question")
    r = await client.post(
        f"{API}/interactions",
        json={
            "pusher_id": rex["id"],
            "note": "  first phrase ",
            "occurred_at": "2026-09-10T08:00:00Z",
            "device_timezone": "Europe/Paris",
            "button_ids": [b["Outside"], b["Play"], b["Outside"]],
            "context_ids": [asking],
        },
    )
    assert r.status_code == 201, r.text
    i = r.json()
    assert i["type"] == "interaction" and i["origin"] == "app"
    assert i["pusher"]["id"] == rex["id"] and i["pusher"]["name"] == "Rex"
    assert i["note"] == "first phrase" and i["is_favourite"] is False and i["is_hidden"] is False
    assert [(p["press_order"], p["text"], p["occurred_at"]) for p in i["presses"]] == [
        (0, "Outside", None),
        (1, "Play", None),
        (2, "Outside", None),
    ]
    assert i["num_presses"] == 3 and i["duration_seconds"] == 0
    assert [c["text"] for c in i["contexts"]] == ["Asking Question"]
    assert i["modeled_pushers"] == []

    got = await client.get(f"{API}/interactions/{i['id']}")
    assert got.status_code == 200 and got.json() == i

    at = {"occurred_at": "2026-09-10T08:00:00Z"}
    for bad in ({"button_ids": [999]}, {"context_ids": [999]}, {"pusher_id": 999}):
        assert (await client.post(f"{API}/interactions", json=at | bad)).status_code == 422
    as_user("u2", "bob@example.com")
    assert (await client.get(f"{API}/interactions/{i['id']}")).status_code == 404


async def test_modeling_rule(client, as_user):
    rex, b = await setup(client, as_user)
    ann = next(p for p in (await client.get(f"{API}/pushers")).json() if p["is_human"])
    nobody_home = await ctx_id(client, "Nobody Home")  # learner-only
    modeled = await ctx_id(client, "Modeled")  # human-only
    at = {"occurred_at": "2026-09-10T08:00:00Z", "button_ids": [b["Play"]]}

    # human pusher: Modeled attached, the only visible learner becomes the modeled pusher,
    # learner-only contexts dropped
    r = await client.post(
        f"{API}/interactions", json=at | {"pusher_id": ann["id"], "context_ids": [nobody_home]}
    )
    i = r.json()
    assert [c["text"] for c in i["contexts"]] == ["Modeled"]
    assert [p["id"] for p in i["modeled_pushers"]] == [rex["id"]]

    # learner pusher: Modeled and modeled pushers stripped, even when sent explicitly
    r = await client.post(
        f"{API}/interactions",
        json=at
        | {
            "pusher_id": rex["id"],
            "context_ids": [modeled, nobody_home],
            "modeled_pusher_ids": [rex["id"]],
        },
    )
    i = r.json()
    assert [c["text"] for c in i["contexts"]] == ["Nobody Home"]
    assert i["modeled_pushers"] == []

    # unassigned behaves like learner
    r = await client.post(f"{API}/interactions", json=at | {"context_ids": [modeled]})
    assert r.json()["contexts"] == [] and r.json()["modeled_pushers"] == []

    # two visible learners: no automatic modeled pusher
    await client.post(f"{API}/pushers", json={"name": "Tom"})
    r = await client.post(f"{API}/interactions", json=at | {"pusher_id": ann["id"]})
    assert r.json()["modeled_pushers"] == []

    # pushers are ranked by interactions logged
    names = [p["name"] for p in (await client.get(f"{API}/pushers")).json()]
    assert names == ["Ann", "Rex", "Tom"]


async def test_patch_reuses_presses_and_reapplies_modeling(client, as_user):
    rex, b = await setup(client, as_user)
    ann = next(p for p in (await client.get(f"{API}/pushers")).json() if p["is_human"])
    at = {"occurred_at": "2026-09-10T08:00:00Z"}
    i = (
        await client.post(
            f"{API}/interactions",
            json=at | {"pusher_id": rex["id"], "button_ids": [b["Play"], b["Outside"], b["Play"]]},
        )
    ).json()
    ids = {p["press_order"]: p["id"] for p in i["presses"]}

    r = await client.patch(
        f"{API}/interactions/{i['id']}",
        json={
            "button_ids": [b["Outside"], b["Play"], b["Food"]],
            "note": "moved",
            "is_favourite": True,
        },
    )
    assert r.status_code == 200, r.text
    j = r.json()
    assert [(p["press_order"], p["text"]) for p in j["presses"]] == [
        (0, "Outside"),
        (1, "Play"),
        (2, "Food"),
    ]
    # rows for Outside and the first Play were kept, second Play dropped, Food is new
    assert j["presses"][0]["id"] == ids[1] and j["presses"][1]["id"] == ids[0]
    assert j["presses"][2]["id"] not in ids.values()
    assert j["num_presses"] == 3 and j["note"] == "moved" and j["is_favourite"] is True

    # reassigning to the human re-runs modeling; counts follow the pusher
    r = await client.patch(f"{API}/interactions/{i['id']}", json={"pusher_id": ann["id"]})
    assert [c["text"] for c in r.json()["contexts"]] == ["Modeled"]
    assert [p["id"] for p in r.json()["modeled_pushers"]] == [rex["id"]]
    names = [p["name"] for p in (await client.get(f"{API}/pushers")).json()]
    assert names == ["Ann", "Rex"]

    # partial updates leave presses alone; empty list clears them
    r = await client.patch(f"{API}/interactions/{i['id']}", json={"note": None})
    assert r.json()["num_presses"] == 3 and r.json()["note"] is None
    r = await client.patch(f"{API}/interactions/{i['id']}", json={"button_ids": []})
    assert r.json()["presses"] == [] and r.json()["num_presses"] == 0

    assert (
        await client.patch(f"{API}/interactions/{i['id']}", json={"button_ids": [999]})
    ).status_code == 422
    as_user("u2", "bob@example.com")
    assert (
        await client.patch(f"{API}/interactions/{i['id']}", json={"note": "x"})
    ).status_code == 404


async def test_soft_delete(client, as_user):
    rex, b = await setup(client, as_user)
    i = (
        await client.post(
            f"{API}/interactions",
            json={
                "occurred_at": "2026-09-10T08:00:00Z",
                "pusher_id": rex["id"],
                "button_ids": [b["Play"]],
            },
        )
    ).json()
    assert (await client.delete(f"{API}/interactions/{i['id']}")).status_code == 204
    assert (await client.get(f"{API}/interactions/{i['id']}")).status_code == 404
    assert (await client.delete(f"{API}/interactions/{i['id']}")).status_code == 404
    # the button still exists, the press no longer counts toward it
    play = (await client.get(f"{API}/buttons/{b['Play']}")).json()
    assert play["press_count"] == 0


async def test_button_merge_moves_presses(client, as_user):
    rex, b = await setup(client, as_user)
    i = (
        await client.post(
            f"{API}/interactions",
            json={"occurred_at": "2026-09-10T08:00:00Z", "button_ids": [b["Play"], b["Food"]]},
        )
    ).json()
    r = await client.post(
        f"{API}/buttons/merge", json={"source_id": b["Play"], "target_id": b["Food"]}
    )
    assert r.status_code == 200 and r.json()["press_count"] == 2
    presses = (await client.get(f"{API}/interactions/{i['id']}")).json()["presses"]
    assert [(p["text"], p["button_id"]) for p in presses] == [
        ("Food", b["Food"]),
        ("Food", b["Food"]),
    ]


async def test_split(client, as_user):
    from tests.test_bases import SERIAL
    from tests.test_device_events import DEVICE, press

    rex, b = await setup(client, as_user)
    await client.post(f"{API}/bases", json={"serial_number": SERIAL, "name": "Kitchen"})
    await client.patch(f"{API}/bases/{SERIAL}", json={"default_pusher_id": rex["id"]})
    await client.post(
        f"{API}/device/events",
        json=[press("AAA111", "2026-09-10T08:00:00Z"), press("BBB222", "2026-09-10T08:00:05Z")],
        headers=DEVICE,
    )
    asking = await ctx_id(client, "Asking Question")
    (i,) = (await client.post(f"{API}/interactions/search", json={})).json()["items"]
    await client.patch(
        f"{API}/interactions/{i['id']}",
        json={"context_ids": [asking], "note": "two", "is_favourite": True},
    )

    r = await client.post(f"{API}/interactions/{i['id']}/split")
    assert r.status_code == 200, r.text
    parts = r.json()
    assert [(p["occurred_at"], p["num_presses"], p["origin"]) for p in parts] == [
        ("2026-09-10T08:00:00Z", 1, "split"),
        ("2026-09-10T08:00:05Z", 1, "split"),
    ]
    for p in parts:
        assert p["pusher"]["id"] == rex["id"] and [c["text"] for c in p["contexts"]] == [
            "Asking Question"
        ]
        assert p["note"] == "two" and p["is_favourite"] is True and p["duration_seconds"] == 0
    assert [p["presses"][0]["text"] for p in parts] == ["Button AAA111", "Button BBB222"]
    assert (await client.get(f"{API}/interactions/{i['id']}")).status_code == 404
    assert (await client.post(f"{API}/interactions/search", json={})).json()["total"] == 2
    assert (await client.get(f"{API}/pushers")).json()[0]["name"] == "Rex"

    # hand-entered presses have no timestamps: refuse; single press: nothing to split
    manual = (
        await client.post(
            f"{API}/interactions",
            json={"occurred_at": "2026-09-11T08:00:00Z", "button_ids": [b["Play"], b["Food"]]},
        )
    ).json()
    r = await client.post(f"{API}/interactions/{manual['id']}/split")
    assert r.status_code == 422
    r = await client.post(f"{API}/interactions/{parts[0]['id']}/split")
    assert r.status_code == 422


async def test_merge(client, as_user):
    from tests.test_bases import SERIAL
    from tests.test_device_events import DEVICE, press

    rex, b = await setup(client, as_user)
    ann = next(p for p in (await client.get(f"{API}/pushers")).json() if p["is_human"])
    asking, inform = await ctx_id(client, "Asking Question"), await ctx_id(client, "Inform")
    await client.post(f"{API}/bases", json={"serial_number": SERIAL, "name": "Kitchen"})
    await client.post(
        f"{API}/device/events",
        json=[press("AAA111", "2026-09-10T08:00:00Z"), press("BBB222", "2026-09-10T08:00:05Z")],
        headers=DEVICE,
    )
    (from_base,) = (await client.post(f"{API}/interactions/search", json={})).json()["items"]
    target = (
        await client.post(
            f"{API}/interactions",
            json={
                "occurred_at": "2026-09-10T09:00:00Z",
                "pusher_id": rex["id"],
                "button_ids": [b["Play"]],
                "context_ids": [asking],
            },
        )
    ).json()
    later = (
        await client.post(
            f"{API}/interactions",
            json={
                "occurred_at": "2026-09-10T10:00:00Z",
                "pusher_id": ann["id"],
                "button_ids": [b["Food"]],
                "context_ids": [inform],
            },
        )
    ).json()
    assert [p["name"] for p in later["modeled_pushers"]] == ["Rex"]

    r = await client.post(
        f"{API}/interactions/{target['id']}/merge",
        json={"interaction_ids": [from_base["id"], later["id"]]},
    )
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["id"] == target["id"] and m["occurred_at"] == "2026-09-10T08:00:00Z"
    assert m["pusher"]["id"] == rex["id"]
    assert [(p["press_order"], p["text"]) for p in m["presses"]] == [
        (0, "Button AAA111"),
        (1, "Button BBB222"),
        (2, "Play"),
        (3, "Food"),
    ]
    assert m["num_presses"] == 4 and m["duration_seconds"] == 5
    assert [c["text"] for c in m["contexts"]] == ["Asking Question", "Inform"]
    assert m["modeled_pushers"] == []  # learner pusher: Modeled data dropped
    assert (await client.get(f"{API}/interactions/{later['id']}")).status_code == 404
    s = (await client.post(f"{API}/interactions/search", json={})).json()
    assert s["total"] == 1 and s["counts"] == {"communication": 1, "modeling": 0, "unassigned": 0}
    assert [
        (p["name"], p["interactions_count"]) for p in (await client.get(f"{API}/pushers")).json()
    ] == [
        ("Rex", 1),
        ("Ann", 0),
    ]

    assert (
        await client.post(f"{API}/interactions/{target['id']}/merge", json={"interaction_ids": []})
    ).status_code == 422
    assert (
        await client.post(
            f"{API}/interactions/{target['id']}/merge", json={"interaction_ids": [later["id"]]}
        )
    ).status_code == 404


async def test_bulk_operations(client, as_user):
    rex, b = await setup(client, as_user)
    ann = next(p for p in (await client.get(f"{API}/pushers")).json() if p["is_human"])
    at = lambda h: {"occurred_at": f"2026-09-10T{h:02d}:00:00Z"}  # noqa: E731
    i1 = (await client.post(f"{API}/interactions", json=at(8) | {"button_ids": [b["Play"]]})).json()
    i2 = (await client.post(f"{API}/interactions", json=at(9) | {"button_ids": [b["Food"]]})).json()
    i3 = (
        await client.post(f"{API}/interactions", json=at(10) | {"button_ids": [b["Outside"]]})
    ).json()
    bulk = lambda **body: client.post(f"{API}/interactions/bulk", json=body)  # noqa: E731

    r = await bulk(operation="assign", ids=[i1["id"], i2["id"]], pusher_id=ann["id"])
    assert r.status_code == 200 and r.json() == {"affected": 2, "id": None}
    got = (await client.get(f"{API}/interactions/{i1['id']}")).json()
    assert got["pusher"]["id"] == ann["id"] and [c["text"] for c in got["contexts"]] == ["Modeled"]
    assert (await client.post(f"{API}/interactions/search", json={})).json()["counts"] == {
        "communication": 0,
        "modeling": 2,
        "unassigned": 1,
    }
    assert (await bulk(operation="assign", ids=[i1["id"]], pusher_id=999)).status_code == 422
    assert (await bulk(operation="assign", ids=[i1["id"]])).status_code == 422

    r = await bulk(operation="merge", ids=[i2["id"], i3["id"]])
    assert r.json() == {"affected": 2, "id": i2["id"]}
    m = (await client.get(f"{API}/interactions/{i2['id']}")).json()
    assert [p["text"] for p in m["presses"]] == ["Food", "Outside"]
    assert (await client.get(f"{API}/interactions/{i3['id']}")).status_code == 404

    r = await bulk(operation="delete", all=True)
    assert r.json() == {"affected": 2, "id": None}
    assert (await client.post(f"{API}/interactions/search", json={})).json()["total"] == 0
    assert (await bulk(operation="delete")).status_code == 422
    assert (await bulk(operation="delete", ids=[999])).status_code == 404
