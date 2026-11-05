"""Crawler settings. Separate from the API's Settings on purpose.

The API refuses to boot in prod without PUBLIC_URL / CORS_ORIGINS; the CronJob needs neither,
so it only requires DATABASE_URL.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_USER_AGENT = "skku-notice-crawler/0.1 (personal project; list and view pages)"


class CrawlerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str | None = None
    # List pages per run, 10 posts each. A run only needs to reach back past the last run.
    crawl_max_pages: int = Field(default=3, ge=1, le=500)
    # Pause between requests to the school's server.
    crawl_delay_seconds: float = Field(default=1.0, ge=0)
    # Random extra pause, as a fraction of the delay (0.25 = up to 25% longer).
    crawl_jitter: float = Field(default=0.25, ge=0, le=1)
    # Hard cap on HTTP requests (retries included) per run, list and view pages together. The
    # normal run needs about boards * CRAWL_MAX_PAGES + CRAWL_DETAIL_LIMIT.
    crawl_max_requests: int = Field(default=150, ge=1, le=2000)
    # Of those, the most the list pages of all boards may use. The view pages get what is left
    # under CRAWL_MAX_REQUESTS, so a slow list walk cannot starve them completely, nor they it.
    # 10 boards * 3 pages = 30, so 60 leaves room for retries. Raise it with --pages for a backfill.
    crawl_list_requests: int = Field(default=60, ge=1, le=2000)
    # Stop paging as soon as a list page shows nothing new (see app/crawler/run.py). Off by
    # default: a full run also refreshes view counts and notices an edited title on pages 2-3.
    crawl_incremental: bool = False
    crawl_user_agent: str = DEFAULT_USER_AGENT
    # Notices whose title has no deadline get their view page read: at most this many per run
    # (0 = never), newest first, so a backlog is worked off over several runs.
    crawl_detail_limit: int = Field(default=30, ge=0, le=200)
    # Only notices posted within this many days are read; an older deadline is long past.
    crawl_detail_max_age_days: int = Field(default=60, ge=1, le=365)

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
