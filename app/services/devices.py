import re

from fastapi import BackgroundTasks
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BaseButton, BaseStation, Button
from app.services import push
from app.services.buttons import button_words

_SERIAL = re.compile(r"^[A-Z0-9]{3,32}$")


def clean_button_serial(raw: str | None) -> str | None:
    """Uppercase, ≥3 alphanumerics, not all zeros; None when unusable."""
    serial = (raw or "").strip().upper()
    if not _SERIAL.match(serial) or set(serial) == {"0"}:
        return None
    return serial


async def ensure_linked(
    session: AsyncSession, base: BaseStation, serial: str
) -> tuple[BaseButton, bool]:
    """Link `serial` on `base`, creating a connect-origin button if the household has none.

    Returns (base_button, created). The same serial in another household is a different button.
    """
    link = await session.scalar(
        select(BaseButton)
        .join(BaseStation, BaseStation.id == BaseButton.base_id)
        .where(
            BaseStation.household_id == base.household_id,
            BaseButton.button_serial_number == serial,
        )
    )
    if link:
        if not link.desired_deleted:
            link.base_id = base.id  # button moved to another base in the same household
        return link, False
    text, word, normalized = button_words(
        await unique_text(session, base.household_id, f"Button {serial}")
    )
    button = Button(
        household_id=base.household_id,
        origin="connect",
        text=text,
        word=word,
        normalized_word=normalized,
    )
    session.add(button)
    await session.flush()
    link = BaseButton(base_id=base.id, button_id=button.id, button_serial_number=serial)
    session.add(link)
    await session.flush()
    return link, True


async def unique_text(session: AsyncSession, household_id: int, text: str) -> str:
    taken = set(
        await session.scalars(
            select(func.lower(Button.text)).where(
                Button.household_id == household_id,
                Button.deleted_at.is_(None),
                func.lower(Button.text).like(f"{text.lower()}%"),
            )
        )
    )
    candidate, n = text, 1
    while candidate.lower() in taken:
        n += 1
        candidate = f"{text} ({n})"
    return candidate


async def unlink(
    session: AsyncSession, link: BaseButton, button: Button, after_commit: BackgroundTasks
) -> None:
    """Tell the base to drop the button. The row goes away when the device script acks."""
    if link.desired_deleted:
        return
    link.desired_deleted = True
    link.desired_version += 1
    base = await session.get_one(BaseStation, link.base_id)
    after_commit.add_task(
        push.notify_household,
        base.household_id,
        "button_unlinked",
        base_id=base.id,
        text=button.text,
        base=base.name or base.serial_number,
    )
