"""HTTP access to the board: polite (delay between requests) and tolerant of blips (retries)."""

import logging
import time
from collections.abc import Callable
from types import TracebackType
from typing import Protocol

import httpx

from app.crawler.config import DEFAULT_USER_AGENT
from app.crawler.parser import list_page_url

logger = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class FetchError(RuntimeError):
    """A page could not be fetched (after retries)."""


class ListSource(Protocol):
    """What the crawl loop needs; tests substitute a fake."""

    def list_page(self, offset: int) -> str: ...


class Fetcher:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        delay: float = 1.0,
        attempts: int = 3,
        backoff: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "ko"},
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=True,
        )
        self._delay = delay
        self._attempts = attempts
        self._backoff = backoff
        self._sleep = sleep
        self._requests = 0

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get(self, url: str) -> str:
        if self._requests and self._delay:
            self._sleep(self._delay)
        self._requests += 1

        last: Exception | None = None
        for attempt in range(1, self._attempts + 1):
            try:
                resp = self._client.get(url)
            except httpx.TransportError as exc:  # timeouts, resets, DNS, ...
                last = exc
            else:
                if resp.status_code == 200:
                    resp.encoding = "utf-8"
                    return resp.text
                if resp.status_code not in RETRY_STATUS:
                    raise FetchError(f"{url} -> HTTP {resp.status_code}")
                last = FetchError(f"{url} -> HTTP {resp.status_code}")
            if attempt < self._attempts:
                wait = self._backoff * 2 ** (attempt - 1)
                logger.warning("fetch failed (%s); retry %d in %.0fs", last, attempt, wait)
                self._sleep(wait)
        raise FetchError(f"{url}: gave up after {self._attempts} attempts ({last})") from last

    def list_page(self, offset: int) -> str:
        return self.get(list_page_url(offset))
