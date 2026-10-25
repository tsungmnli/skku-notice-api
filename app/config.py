from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator, model_validator
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

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [v.strip().rstrip("/") for v in value.split(",") if v.strip()]
        return value

    @model_validator(mode="after")
    def _prod_guard(self) -> "Settings":
        """Refuse to boot in prod with a half-configured environment."""
        if self.app_env != "prod":
            return self
        missing = [name for name in ("database_url", "public_url") if not getattr(self, name)]
        if missing:
            raise ValueError(f"prod requires: {', '.join(m.upper() for m in missing)}")
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
