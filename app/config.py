from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# The ingress forwards `/api/...` without stripping the prefix, so the app owns it.
API_PREFIX = "/api"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["dev", "test", "prod"] = "dev"
    # Optional until the DB layer lands; required in prod (see guard below).
    database_url: str | None = None
    # Public web origin, e.g. https://skku.example.com
    public_url: str | None = None
    # Comma-separated list of allowed browser origins.
    cors_origins: Annotated[list[str], NoDecode] = []
    # Unset = on in dev/test, off in prod.
    docs_enabled: bool | None = None

    # --- Accounts / auth ---
    # Signs the short-lived OAuth state cookie of the Google sign-in. Session tokens are random
    # and stored hashed, so this secret is not what keeps them unguessable.
    session_secret: str | None = None
    # Sliding window: every use pushes the expiry out again.
    session_ttl_days: int = Field(30, ge=1, le=365)
    # Outgoing mail. For Resend SMTP: host smtp.resend.com, user "resend", password = API key.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "SKKU 공지 <noreply@skkunoti.tsungmn.com>"
    # Google sign-in is optional; both values or neither.
    google_client_id: str | None = None
    google_client_secret: str | None = None
    # Cloudflare Turnstile secret (server-side verification of the sign-up form).
    turnstile_secret_key: str | None = None
    # Proxies between the client and this app (Cloudflare + ingress = 2). Rate limits key on the
    # client IP, so a wrong count either merges everyone into one bucket or lets X-Forwarded-For
    # be forged. None = not decided yet; prod refuses to boot until it is set (0 is valid).
    trusted_proxy_hops: int | None = Field(None, ge=0, le=10)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [v.strip().rstrip("/") for v in value.split(",") if v.strip()]
        return value

    @model_validator(mode="after")
    def _google_pair(self) -> "Settings":
        if bool(self.google_client_id) != bool(self.google_client_secret):
            raise ValueError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set together")
        if self.google_client_id and not self.session_secret:
            raise ValueError("Google sign-in needs SESSION_SECRET (signs the OAuth state)")
        return self

    @model_validator(mode="after")
    def _prod_guard(self) -> "Settings":
        """Refuse to boot in prod with a half-configured environment."""
        if self.app_env != "prod":
            return self
        required = (
            "database_url",
            "public_url",
            "smtp_host",
            "smtp_user",
            "smtp_password",
            "turnstile_secret_key",
        )
        missing = [name for name in required if not getattr(self, name)]
        if missing:
            raise ValueError(f"prod requires: {', '.join(m.upper() for m in missing)}")
        if not self.session_secret or len(self.session_secret) < 32:
            raise ValueError("prod requires SESSION_SECRET of at least 32 characters")
        if self.trusted_proxy_hops is None:
            raise ValueError("prod requires TRUSTED_PROXY_HOPS (0 if nothing sits in front)")
        if not self.cors_origins:
            raise ValueError("prod requires CORS_ORIGINS")
        if "*" in self.cors_origins:
            raise ValueError("CORS_ORIGINS must not contain '*' in prod")
        return self

    @property
    def sqlalchemy_url(self) -> str:
        """DATABASE_URL as a SQLAlchemy URL on the psycopg 3 driver.

        Secrets/Vault usually hold a plain libpq URL (`postgres://` or `postgresql://`).
        """
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is not set")
        url = self.database_url
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url

    @property
    def docs_on(self) -> bool:
        if self.docs_enabled is not None:
            return self.docs_enabled
        return self.app_env != "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
