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
