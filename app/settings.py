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
    # AI (PRD §12). No key and no token = every /ai/* endpoint answers 503 ai_unavailable.
    anthropic_api_key: str = ""  # x-api-key (Anthropic)
    anthropic_auth_token: str = ""  # Authorization: Bearer (gateways such as Token Harbor)
    anthropic_base_url: str = ""  # blank = api.anthropic.com; any Messages-API-compatible host
    ai_model: str = "claude-opus-5"
    ai_timeout_seconds: int = 30  # per request; free gateway lanes queue for a minute or more

    @property
    def ai_configured(self) -> bool:
        return bool(self.anthropic_api_key.strip() or self.anthropic_auth_token.strip())

    @property
    def ai_is_claude(self) -> bool:
        """Claude-only request extras (effort, structured outputs, cache_control) are sent
        only to Claude models; other models behind a gateway get plain Messages requests."""
        return self.ai_model.startswith("claude")

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
