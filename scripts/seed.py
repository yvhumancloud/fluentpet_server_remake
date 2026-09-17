"""Demo data: a household with pushers, buttons, a base and months of activity.

Local (dev tokens):   PYTHONPATH=. uv run python scripts/seed.py [--days 14] [--per-day 4]
    wipes and recreates the demo households; sign in with `Authorization: Bearer ann` (admin),
    `bob` (member) or `dan` (separate, empty household) — needs DEV_TOKENS in .env.

Prod (a real account): sign in once from the app, then on the EC2 box
    cd /opt/fluentpet && sudo docker compose exec api \
        python -m scripts.seed --email you@gmail.com --days 180 --per-day 10
    wipes that user's household and rebuilds it with the same content (you stay admin; Firebase
    uid is kept, so the app keeps working). Nothing else in the database is touched.

Uses the same services as the API so every rule (word normalisation, modeling, counters) holds.
"""

import argparse
import asyncio
import os
import random
from datetime import UTC, date, datetime, timedelta

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://fluentpet:fluentpet@localhost:5432/fluentpet"
)

from sqlalchemy import delete, exists, select  # noqa: E402

from app.auth import provision_user  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    BaseButton,
    BaseStation,
    Button,
    ButtonPress,
    Context,
    Household,
    HouseholdInvitation,
    Interaction,
    LearnerType,
    Note,
    Preference,
    Pusher,
    User,
)
from app.services import interactions as svc  # noqa: E402
from app.services.buttons import button_words  # noqa: E402

USERS = {
    "ann": {"uid": "dev-ann", "email": "ann@example.com", "name": "Ann"},
    "bob": {"uid": "dev-bob", "email": "bob@example.com", "name": "Bob"},
    "dan": {"uid": "dev-dan", "email": "dan@example.com", "name": "Dan"},
}
BUTTONS = [
    ("Play", None),
    ("Outside", "front door"),
    ("Food", None),
    ("Water", None),
    ("Walk", None),
    ("Mom", None),
    ("Dad", None),
    ("Love you ❤️", None),
    ("Bye", None),
    ("Cuddle", None),
    ("Scritches", None),
    ("Hmm?", "rarely used"),
]
LINKED = {"Play": "FPB1A2B3C4D5", "Outside": "FPB2B3C4D5E6", "Food": "FPB3C4D5E6F7"}
SERIAL = "FPB000000001"


async def run(days: int = 14, per_day: int | None = None, email: str | None = None) -> dict:
    """Seed `days` of history. `email`: rebuild that existing user's household instead of ann's."""
    rng = random.Random(7)
    now = datetime.now(UTC)
    async with SessionLocal() as session, session.begin():
        admin, tz = USERS["ann"], "Europe/Paris"
        if email:
            u = await session.scalar(select(User).where(User.email == email.lower()))
            if u is None:
                raise SystemExit(f"{email}: no such user — sign in from the app once first")
            admin, tz = {"uid": u.firebase_uid, "email": u.email, "name": u.full_name}, u.timezone
        # start over: users go first (households restrict while they have members), then the
        # households cascade to everything they own
        uids = [admin["uid"], USERS["bob"]["uid"]] + ([] if email else [USERS["dan"]["uid"]])
        old = list(
            await session.scalars(select(User.household_id).where(User.firebase_uid.in_(uids)))
        )
        await session.execute(delete(User).where(User.firebase_uid.in_(uids)))
        await session.execute(
            delete(Household).where(
                Household.id.in_(old), ~exists().where(User.household_id == Household.id)
            )
        )

        ann = await provision_user(session, admin)
        ann.timezone = tz
        hh = ann.household_id
        b = USERS["bob"]
        bob = User(firebase_uid=b["uid"], email=b["email"], full_name=b["name"], household_id=hh)
        session.add(bob)
        await session.flush()
        session.add(Pusher(household_id=hh, name="Bob", is_human=True))
        session.add(
            HouseholdInvitation(
                household_id=hh,
                inviting_user_id=ann.id,
                email="carol@example.com",
                expires_at=now + timedelta(hours=72),
            )
        )
        types = {t.name: t.id for t in await session.scalars(select(LearnerType))}
        rex = Pusher(
            household_id=hh,
            name="Rex",
            learner_type_id=types["dog"],
            sub_type="Border Collie",
            sex="male",
            birth_date=date(2022, 3, 14),
            training_started_at=date.today() - timedelta(days=120),
            country="FR",
            language="en",
        )
        tom = Pusher(household_id=hh, name="Tom", learner_type_id=types["cat"], sex="female")
        session.add_all([rex, tom])
        await session.flush()

        buttons: dict[str, Button] = {}
        for text_, note in BUTTONS:
            t, w, n = button_words(text_)
            b = Button(
                household_id=hh,
                text=t,
                word=w,
                normalized_word=n,
                note=note,
                is_hidden=text_ == "Hmm?",
                introduced_at=date.today() - timedelta(days=rng.randrange(10, days + 60)),
            )
            session.add(b)
            buttons[t] = b
        await session.flush()
        bedtime = Context(household_id=hh, text="Bedtime")
        session.add(bedtime)
        ctx = {c.text: c.id for c in await session.scalars(select(Context))}

        base = BaseStation(
            household_id=hh,
            serial_number=SERIAL,
            name="Kitchen",
            default_pusher_id=rex.id,
            fw_version="2.1.0",
            battery_level=80,
            battery_updated_at=now - timedelta(minutes=5),
            last_online_at=now - timedelta(minutes=5),
            reported_state={"charging": True, "wifi": "home"},
            created_by_user_id=ann.id,
        )
        session.add(base)
        await session.flush()
        for text_, serial in LINKED.items():
            session.add(
                BaseButton(
                    base_id=base.id,
                    button_id=buttons[text_].id,
                    button_serial_number=serial,
                    battery_level=rng.randrange(40, 100),
                    battery_updated_at=now - timedelta(hours=1),
                    last_online_at=now - timedelta(minutes=5),
                    applied_version=1,
                )
            )

        me = await session.scalar(  # the admin's own human pusher (created at provisioning)
            select(Pusher.id).where(Pusher.household_id == hh, Pusher.is_human).order_by(Pusher.id)
        )
        vocabulary = [t for t, _ in BUTTONS if t != "Hmm?"]
        learner_ctx = ("Asking Question", "Request Action/Object", "Inform", "Bedtime")
        lo, hi = (2, 6) if per_day is None else (max(1, per_day // 2), per_day + per_day // 2 + 1)
        count = 0
        for day in range(days, -1, -1):
            for _ in range(rng.randrange(lo, hi)):
                at = now - timedelta(
                    days=day, hours=rng.randrange(6, 22), minutes=rng.randrange(60)
                )
                from_base = rng.random() < 0.6  # a base can only report its linked buttons
                words = rng.sample(
                    list(LINKED) if from_base else vocabulary, rng.choice([1, 1, 2, 2, 3])
                )
                who = rng.choice(
                    [rex.id, rex.id, rex.id, tom.id, me, None if from_base else rex.id]
                )
                i = Interaction(
                    household_id=hh,
                    pusher_id=who,
                    occurred_at=at,
                    device_timezone=tz,
                    origin="base" if from_base else "app",
                    is_favourite=rng.random() < 0.1,
                    note=rng.choice([None, None, None, "Wanted dinner early", "Very insistent"]),
                    created_by_user_id=None if from_base else ann.id,
                    created_by_base_id=base.id if from_base else None,
                )
                session.add(i)
                await session.flush()
                if from_base:
                    session.add_all(
                        ButtonPress(
                            interaction_id=i.id,
                            button_id=buttons[w].id,
                            press_order=k,
                            occurred_at=at + timedelta(seconds=3 * k),
                        )
                        for k, w in enumerate(words)
                    )
                    await session.flush()
                else:
                    await svc.set_presses(session, i, [buttons[w].id for w in words])
                if who is not None and rng.random() < 0.7:
                    await svc.set_contexts(session, i, [ctx[rng.choice(learner_ctx)]])
                await svc.apply_modeling(session, i)
                await svc.finalize(session, [i.id], [who])
                count += 1
        hidden = await session.scalar(
            select(Interaction).where(Interaction.household_id == hh).limit(1)
        )
        hidden.is_hidden = True
        notes = ("Vet says all good", "New button introduced", "Skipped training", "Great session")
        session.add_all(
            Note(
                household_id=hh,
                text=notes[d % len(notes)],
                occurred_at=now - timedelta(days=d, hours=3),
                device_timezone=tz,
                created_by_user_id=ann.id,
                is_favourite=d == 2,
            )
            for d in (1, 2, 6, *range(10, days, 5))
        )
        session.add_all(
            [
                Preference(user_id=ann.id, key="push_frequency", value="all"),
                Preference(user_id=ann.id, key="default_pusher_id", value=rex.id),
                Preference(user_id=ann.id, key="activity_sort", value="occurred_at_desc"),
                Preference(user_id=bob.id, key="push_frequency", value="on_interaction"),
            ]
        )
        if not email:
            await provision_user(session, USERS["dan"])
        return {"household_id": hh, "interactions": count, "base": SERIAL}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--days", type=int, default=14, help="days of history (default 14)")
    ap.add_argument("--per-day", type=int, help="average interactions per day (default 2-5)")
    ap.add_argument(
        "--email", help="rebuild this existing user's household instead of the demo one"
    )
    a = ap.parse_args()
    info = asyncio.run(run(a.days, a.per_day, a.email))
    print(f"seeded household {info['household_id']}: {info['interactions']} interactions")
    if not a.email:
        print("sign in with  Authorization: Bearer ann | bob | dan   (DEV_TOKENS in .env)")
    print("device calls: X-Device-Key, serial", SERIAL)
