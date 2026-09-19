import os
from types import SimpleNamespace

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://fluentpet:fluentpet@localhost:5432/fluentpet_test"
)
os.environ["DEVICE_API_KEY"] = "test-device-key"
os.environ["ANTHROPIC_API_KEY"] = "test-anthropic-key"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.auth import get_claims  # noqa: E402
from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402

SEEDED = {"learner_types", "button_concepts", "contexts"}


@pytest.fixture(scope="session", autouse=True)
def migrated():
    cfg = Config("alembic.ini")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(autouse=True)
async def clean_db():
    yield
    # households is deleted, not truncated: a cascade truncate would wipe the seeded contexts.
    tables = ", ".join(t for t in Base.metadata.tables if t not in SEEDED | {"households"})
    async with engine.begin() as conn:
        await conn.execute(text(f"truncate {tables} restart identity cascade"))
        await conn.execute(text("delete from households"))
        await conn.execute(text("alter sequence households_id_seq restart"))
    app.dependency_overrides.clear()


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def as_user():
    """as_user("uid", "a@b.c", name="A", admin=False): later requests run as that user."""

    def _set(uid: str, email: str, *, name: str | None = None, admin: bool = False):
        claims = {"uid": uid, "email": email, "name": name}
        if admin:
            claims["admin"] = True
        app.dependency_overrides[get_claims] = lambda: claims

    return _set


class FakeS3:
    """Stands in for the boto3 R2 client. Records objects, signs URLs without a network."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, *, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    def delete_object(self, *, Bucket, Key):
        self.objects.pop(Key, None)

    def generate_presigned_url(self, method, *, Params, ExpiresIn):
        return f"https://r2.test/{Params['Key']}?expires={ExpiresIn}"


@pytest.fixture(autouse=True)
def s3(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr("app.services.storage.client", lambda: fake)
    return fake


class FakeFCM:
    """Stands in for firebase messaging. `errors[token]` makes that token fail with that error."""

    def __init__(self):
        self.sent: list[dict] = []
        self.errors: dict[str, str] = {}

    def __call__(self, tokens, title, body, data):
        self.sent.append({"tokens": list(tokens), "title": title, "body": body, "data": data})
        return [self.errors.get(t) for t in tokens]

    def keys(self, token: str | None = None) -> list[str]:
        return [m["data"]["key"] for m in self.sent if token is None or token in m["tokens"]]


@pytest.fixture(autouse=True)
def fcm(monkeypatch):
    fake = FakeFCM()
    monkeypatch.setattr("app.services.push.fcm_send", fake)
    return fake


class FakeWebhooks:
    """Stands in for the outbound HTTP GET. `fail[url]` = number of attempts to fail first."""

    def __init__(self):
        self.calls: list[str] = []
        self.fail: dict[str, int] = {}

    async def __call__(self, url: str) -> int:
        self.calls.append(url)
        if self.fail.get(url, 0) > 0:
            self.fail[url] -= 1
            raise ConnectionError("boom")
        return 200


@pytest.fixture(autouse=True)
def webhooks(monkeypatch):
    fake = FakeWebhooks()
    monkeypatch.setattr("app.services.webhooks.get", fake)
    return fake


class FakeClaude:
    """Stands in for AsyncAnthropic. Queue model output with `.reply(...)`; calls are recorded.

    A reply is the structured object for `messages.parse`, or the text for `messages.create`.
    `error` makes the next call raise it (any anthropic.APIError subclass).
    """

    def __init__(self):
        self.calls: list[dict] = []
        self.replies: list = []
        self.error: Exception | None = None
        self.messages = self  # client.messages.parse / client.messages.create

    def reply(self, value):
        self.replies.append(value)
        return self

    def _take(self, kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return self.replies.pop(0)

    @staticmethod
    def _usage():
        return SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0)

    async def parse(self, **kw):
        out = kw["output_format"].model_validate(self._take(kw))
        return SimpleNamespace(parsed_output=out, usage=self._usage(), model=kw["model"])

    async def create(self, **kw):
        text_ = self._take(kw)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text_)],
            usage=self._usage(),
            model=kw["model"],
            stop_reason="end_turn",
        )


@pytest.fixture(autouse=True)
def claude(monkeypatch):
    fake = FakeClaude()
    monkeypatch.setattr("app.services.ai.client", lambda: fake)
    return fake
