import pytest

from app.settings import Settings


async def test_dev_tokens_stand_in_for_firebase(client, monkeypatch):
    monkeypatch.setattr("app.auth.settings.dev_tokens", "ann:ann@example.com:Ann,bob:bob@x.com")
    r = await client.get("/api/v1/me", headers={"Authorization": "Bearer ann"})
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "ann@example.com" and r.json()["full_name"] == "Ann"
    r = await client.get("/api/v1/me", headers={"Authorization": "Bearer bob"})
    assert r.status_code == 200 and r.json()["full_name"] is None
    assert (
        await client.get("/api/v1/me", headers={"Authorization": "Bearer nope"})
    ).status_code == 401
    assert (await client.get("/api/v1/me")).status_code == 401


def test_dev_tokens_refused_in_prod():
    with pytest.raises(ValueError, match="DEV_TOKENS"):
        Settings(
            env="prod", dev_tokens="ann:ann@example.com", database_url="postgresql+asyncpg://x"
        )
    assert Settings(
        env="dev", dev_tokens="ann:ann@example.com", database_url="postgresql+asyncpg://x"
    )


def test_database_url_accepts_neon_string_as_printed():
    neon = "postgresql://u:p@ep-x.ap-southeast-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    assert (
        Settings(database_url=neon).database_url
        == "postgresql+asyncpg://u:p@ep-x.ap-southeast-1.aws.neon.tech/neondb?ssl=require"
    )
    flipped = "postgres://u:p@h/db?channel_binding=require&sslmode=require"
    assert (
        Settings(database_url=flipped).database_url == "postgresql+asyncpg://u:p@h/db?ssl=require"
    )
    local = "postgresql+asyncpg://fluentpet:fluentpet@localhost:5432/fluentpet"
    assert Settings(database_url=local).database_url == local
