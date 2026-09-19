import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str
    firebase_credentials_json: str = ""
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    device_api_key: str = ""
    job_api_key: str = ""  # X-Job-Key for /internal/* (triggered by hand)
    sentry_dsn: str = ""
    anthropic_api_key: str = ""  # blank = every /ai/* endpoint answers 503 ai_unavailable
    ai_model: str = "claude-opus-5"
    # "token:email[:name],..." — bearer tokens that stand in for Firebase in dev/e2e. Never prod.
    dev_tokens: str = ""

    @field_validator("database_url")
    @classmethod
    def _asyncpg_url(cls, v: str) -> str:
        """Accept the URL as Neon prints it: asyncpg wants +asyncpg, ssl= and no channel_binding."""
        u = urlsplit(re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", v))
        q = [
            ("ssl" if k == "sslmode" else k, x)
            for k, x in parse_qsl(u.query)
            if k != "channel_binding"
        ]
        return urlunsplit(u._replace(query=urlencode(q)))

    @model_validator(mode="after")
    def _no_dev_tokens_in_prod(self):
        if self.env == "prod" and self.dev_tokens:
            raise ValueError("DEV_TOKENS must be empty when ENV=prod")
        return self

    def dev_claims(self, token: str) -> dict | None:
        for entry in filter(None, self.dev_tokens.split(",")):
            name, email, *rest = entry.strip().split(":")
            if name == token:
                return {"uid": f"dev-{name}", "email": email, "name": rest[0] if rest else None}
        return None


settings = Settings()
