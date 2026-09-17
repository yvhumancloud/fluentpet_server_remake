"""Search load test: seeds one household with N interactions, then times POST /interactions/search.

    uv run python scripts/loadtest.py                # 50k rows, 200 requests × 10 in flight
    uv run python scripts/loadtest.py --interactions 5000 --requests 50

Runs the ASGI app in-process (no uvicorn, no network) against DATABASE_URL, so the numbers are
app + Postgres time. Auth is bypassed for the seeded user only, inside this process.
"""

import argparse
import asyncio
import os
import random
import statistics
import time
from datetime import UTC, datetime, timedelta

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://fluentpet:fluentpet@localhost:5432/fluentpet"
)

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import func, insert, select, text  # noqa: E402

from app.auth import get_claims, provision_user  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Button,
    ButtonPress,
    Context,
    Interaction,
    InteractionContext,
    Note,
    Pusher,
    User,
)

EMAIL = "load@example.com"
CLAIMS = {"uid": "load-uid", "email": EMAIL, "name": "Load"}
CHUNK = 1500  # asyncpg caps a statement at 32767 bind params


async def seed(n_interactions: int, n_notes: int) -> None:
    rng = random.Random(42)
    async with SessionLocal() as session, session.begin():
        user = await session.scalar(select(User).where(User.email == EMAIL))
        if user is None:
            user = await provision_user(session, CLAIMS)
        hh = user.household_id
        have = await session.scalar(
            select(func.count()).where(
                Interaction.household_id == hh, Interaction.deleted_at.is_(None)
            )
        )
        if have >= n_interactions:
            print(f"seed: household {hh} already has {have} interactions")
            return
        print(f"seed: household {hh} has {have}, adding {n_interactions - have} …")
        pushers = list(await session.scalars(select(Pusher.id).where(Pusher.household_id == hh)))
        if len(pushers) < 3:
            session.add_all([Pusher(household_id=hh, name=n) for n in ("Rex", "Tom")])
            await session.flush()
            pushers = list(
                await session.scalars(select(Pusher.id).where(Pusher.household_id == hh))
            )
        buttons = list(await session.scalars(select(Button.id).where(Button.household_id == hh)))
        if len(buttons) < 40:
            session.add_all(
                Button(
                    household_id=hh, text=f"word{i}", word=f"word{i}", normalized_word=f"word{i}"
                )
                for i in range(len(buttons), 40)
            )
            await session.flush()
            buttons = list(
                await session.scalars(select(Button.id).where(Button.household_id == hh))
            )
        contexts = list(
            await session.scalars(select(Context.id).where(Context.household_id.is_(None)))
        )
        start = datetime.now(UTC) - timedelta(days=730)
        todo = n_interactions - have
        for offset in range(0, todo, CHUNK):
            rows = []
            for _ in range(min(CHUNK, todo - offset)):
                at = start + timedelta(seconds=rng.randrange(730 * 86400))
                rows.append(
                    {
                        "household_id": hh,
                        "pusher_id": rng.choice([*pushers, None]) if rng.random() < 0.9 else None,
                        "note": f"note {rng.randrange(1000)} dinner"
                        if rng.random() < 0.3
                        else None,
                        "occurred_at": at,
                        "origin": rng.choice(["app", "base", "base"]),
                        "is_favourite": rng.random() < 0.05,
                        "is_hidden": rng.random() < 0.1,
                        "deleted_at": at if rng.random() < 0.05 else None,
                        "created_by_user_id": user.id,
                    }
                )
            ids = list(
                await session.scalars(insert(Interaction).values(rows).returning(Interaction.id))
            )
            presses, links, counts = [], [], []
            for iid, row in zip(ids, rows, strict=True):
                n = rng.choice([1, 1, 1, 2, 2, 3, 4])
                for order in range(n):
                    presses.append(
                        {
                            "interaction_id": iid,
                            "button_id": rng.choice(buttons),
                            "press_order": order,
                            "occurred_at": row["occurred_at"] + timedelta(seconds=order * 2)
                            if row["origin"] == "base"
                            else None,
                        }
                    )
                for c in rng.sample(contexts, rng.choice([0, 0, 1, 1, 2])):
                    links.append({"interaction_id": iid, "context_id": c})
                counts.append({"id": iid, "num_presses": n, "duration_seconds": (n - 1) * 2.0})
            await session.execute(insert(ButtonPress).values(presses))
            if links:
                await session.execute(insert(InteractionContext).values(links))
            await session.execute(
                text(
                    "update interactions i set num_presses = c.n, duration_seconds = c.d "
                    "from (select unnest(cast(:ids as bigint[])) id, "
                    "unnest(cast(:ns as int[])) n, unnest(cast(:ds as float[])) d) c "
                    "where i.id = c.id"
                ),
                {
                    "ids": [c["id"] for c in counts],
                    "ns": [c["num_presses"] for c in counts],
                    "ds": [c["duration_seconds"] for c in counts],
                },
            )
            print(f"  {offset + len(rows)}/{todo}")
        notes = [
            {
                "household_id": hh,
                "text": f"note {rng.randrange(1000)} vet",
                "occurred_at": start + timedelta(seconds=rng.randrange(730 * 86400)),
                "created_by_user_id": user.id,
            }
            for _ in range(n_notes)
        ]
        await session.execute(insert(Note).values(notes))
        await session.execute(
            text(
                "update pushers p set interactions_count = (select count(*) from interactions i "
                "where i.pusher_id = p.id and i.deleted_at is null) where p.household_id = :hh"
            ),
            {"hh": hh},
        )


async def scenarios(client: AsyncClient) -> dict[str, dict]:
    pushers = (await client.get("/api/v1/pushers")).json()
    buttons = (await client.get("/api/v1/buttons")).json()
    contexts = (await client.get("/api/v1/contexts", params={"kind": "learner"})).json()
    since = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    return {
        "feed p1": {},
        "feed p200": {"page": 200},
        "created_at sort": {"sort": "created_at_desc"},
        "unassigned": {"tab": "unassigned"},
        "pusher": {"filters": {"pusher_ids": [pushers[0]["id"]]}},
        "button any": {"filters": {"button_ids": [b["id"] for b in buttons[:3]]}},
        "button all": {"filters": {"button_ids": [b["id"] for b in buttons[:2]], "match": "all"}},
        "context any": {"filters": {"context_ids": [c["id"] for c in contexts[:3]]}},
        "text": {"filters": {"text": "dinner"}},
        "last 30d": {"filters": {"from": since}},
        "multi+fav": {"filters": {"presses": "multiple", "favourites": "only"}},
        "notes only": {"filters": {"notes": "only"}},
        "with hidden": {"filters": {"include_hidden": True}},
    }


async def measure(client: AsyncClient, body: dict, n: int, concurrency: int) -> list[float]:
    times: list[float] = []
    sem = asyncio.Semaphore(concurrency)

    async def one() -> None:
        async with sem:
            t = time.perf_counter()
            r = await client.post("/api/v1/interactions/search", json=body)
            times.append((time.perf_counter() - t) * 1000)
            assert r.status_code == 200, r.text

    await asyncio.gather(*(one() for _ in range(n)))
    return times


def p(times: list[float], q: float) -> float:
    return statistics.quantiles(times, n=100)[int(q) - 1] if len(times) >= 2 else times[0]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interactions", type=int, default=50_000)
    ap.add_argument("--notes", type=int, default=2_000)
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--budget-ms", type=float, default=300)
    args = ap.parse_args()

    await seed(args.interactions, args.notes)
    app.dependency_overrides[get_claims] = lambda: CLAIMS
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://load") as client:
        total = (await client.post("/api/v1/interactions/search", json={})).json()["total"]
        print(f"\nfeed total={total}, {args.requests} requests × {args.concurrency} in flight\n")
        print(f"{'scenario':<16}{'p50':>8}{'p95':>8}{'max':>8}  ")
        worst = 0.0
        for name, body in (await scenarios(client)).items():
            await measure(client, body, 3, 1)  # warm
            t = await measure(client, body, args.requests, args.concurrency)
            p95 = p(t, 95)
            worst = max(worst, p95)
            flag = "" if p95 <= args.budget_ms else "  <-- over budget"
            print(f"{name:<16}{p(t, 50):>8.0f}{p95:>8.0f}{max(t):>8.0f}{flag}")
        print(f"\nworst p95 {worst:.0f} ms (budget {args.budget_ms:.0f} ms)")


if __name__ == "__main__":
    asyncio.run(main())
