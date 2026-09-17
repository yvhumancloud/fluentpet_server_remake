"""Demo data for e2e testing against the compose API.

    PYTHONPATH=. uv run python scripts/seed.py     # wipes and recreates the demo households

Sign in with `Authorization: Bearer ann` (household admin), `bob` (member) or `dan` (a separate,
empty household) — needs DEV_TOKENS in .env, see .env.example. Uses the same services as the
API so every rule (word normalisation, modeling, counters) holds for the seeded rows.
"""

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


async def run() -> dict:
    rng = random.Random(7)
    now = datetime.now(UTC)
    async with SessionLocal() as session, session.begin():
        # start over: users go first (households restrict while they have members), then the
        # households cascade to everything they own
        uids = [u["uid"] for u in USERS.values()]
        old = list(
            await session.scalars(select(User.household_id).where(User.firebase_uid.in_(uids)))
        )
        await session.execute(delete(User).where(User.firebase_uid.in_(uids)))
        await session.execute(
            delete(Household).where(
                Household.id.in_(old), ~exists().where(User.household_id == Household.id)
            )
        )

        ann = await provision_user(session, USERS["ann"])
        ann.timezone = "Europe/Paris"
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
                introduced_at=date.today() - timedelta(days=rng.randrange(10, 200)),
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

        human = {
            p.name: p.id
            for p in await session.scalars(select(Pusher).where(Pusher.household_id == hh))
        }
        learner_ctx = ("Asking Question", "Request Action/Object", "Inform", "Bedtime")
        count = 0
        for day in range(14, -1, -1):
            for _ in range(rng.randrange(2, 6)):
                at = now - timedelta(
                    days=day, hours=rng.randrange(6, 22), minutes=rng.randrange(60)
                )
                words = rng.sample(list(LINKED), rng.choice([1, 1, 2, 2, 3]))
                from_base = rng.random() < 0.6
                who = rng.choice(
                    [rex.id, rex.id, rex.id, tom.id, human["Ann"], None if from_base else rex.id]
                )
                i = Interaction(
                    household_id=hh,
                    pusher_id=who,
                    occurred_at=at,
                    device_timezone="Europe/Paris",
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
        session.add_all(
            Note(
                household_id=hh,
                text=t,
                occurred_at=now - timedelta(days=d, hours=3),
                device_timezone="Europe/Paris",
                created_by_user_id=ann.id,
                is_favourite=d == 2,
            )
            for d, t in (
                (1, "Vet says all good"),
                (2, "New button Walk introduced"),
                (6, "Skipped training"),
            )
        )
        session.add_all(
            [
                Preference(user_id=ann.id, key="push_frequency", value="all"),
                Preference(user_id=ann.id, key="default_pusher_id", value=rex.id),
                Preference(user_id=ann.id, key="activity_sort", value="occurred_at_desc"),
                Preference(user_id=bob.id, key="push_frequency", value="on_interaction"),
            ]
        )
        await provision_user(session, USERS["dan"])
        return {"household_id": hh, "interactions": count, "base": SERIAL}


if __name__ == "__main__":
    info = asyncio.run(run())
    print(f"seeded household {info['household_id']}: {info['interactions']} interactions")
    print("sign in with  Authorization: Bearer ann | bob | dan   (DEV_TOKENS in .env)")
    print("device calls: X-Device-Key from .env, serial", SERIAL)
