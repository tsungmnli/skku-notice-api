"""Crawler settings. Separate from the API's Settings on purpose.

The API refuses to boot in prod without PUBLIC_URL / CORS_ORIGINS; the CronJob needs neither,
so it only requires DATABASE_URL.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_USER_AGENT = "skku-notice-crawler/0.1 (personal project; list pages only)"


class CrawlerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str | None = None
    # List pages per run, 10 posts each. A run only needs to reach back past the last run.
    crawl_max_pages: int = Field(default=3, ge=1, le=500)
    # Pause between requests to the school's server.
    crawl_delay_seconds: float = Field(default=1.0, ge=0)
    crawl_user_agent: str = DEFAULT_USER_AGENT

    @property
    def sqlalchemy_url(self) -> str:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is not set")
        url = self.database_url
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url


@lru_cache
def get_crawler_settings() -> CrawlerSettings:
    return CrawlerSettings()
