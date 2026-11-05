"""HTTP access to the board: polite (delay between requests) and tolerant of blips (retries)."""

import logging
import random
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Protocol

import httpx2

from app.crawler.config import DEFAULT_USER_AGENT
from app.crawler.parser import BOARD_URL, article_url, list_page_url

logger = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}

# A server that asks us to wait longer than this is not worth waiting for inside one CronJob run:
# give up on the page and let the next run try again.
MAX_RETRY_AFTER = 60.0


class FetchError(RuntimeError):
    """A page could not be fetched (after retries)."""


class RequestBudgetExceeded(FetchError):
    """The run already sent as many requests as it was allowed to (`max_requests`)."""


def retry_after_seconds(
    value: str | None, *, now: Callable[[], datetime] | None = None
) -> float | None:
    """`Retry-After` as seconds to wait: either a number of seconds or an HTTP date. None when the
    header is absent or unreadable (the normal backoff then applies)."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    current = now() if now else datetime.now(UTC)
    return max(0.0, (when - current).total_seconds())


class ListSource(Protocol):
    """What the crawl loop needs; tests substitute a fake."""

    def list_page(self, offset: int) -> str: ...


class DetailSource(Protocol):
    """Fetches one notice's view page; tests substitute a fake."""

    def detail_page(self, article_no: int, list_url: str = BOARD_URL) -> str: ...


class BoardFetcher:
    """A `Fetcher` aimed at one board, which is the `list_page(offset)` the crawl loop expects.

    It only forwards: the pause between requests and the request budget stay the fetcher's, so
    all boards of a run share them.
    """

    def __init__(self, fetcher: "Fetcher", list_url: str) -> None:
        self._fetcher = fetcher
        self._list_url = list_url

    def list_page(self, offset: int) -> str:
        return self._fetcher.list_page(offset, self._list_url)

    def detail_page(self, article_no: int) -> str:
        return self._fetcher.detail_page(article_no, self._list_url)


class Fetcher:
    def __init__(
        self,
        *,
        client: httpx2.Client | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        delay: float = 1.0,
        attempts: int = 3,
        backoff: float = 2.0,
        max_requests: int | None = None,
        jitter: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._client = client or httpx2.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "ko"},
            timeout=httpx2.Timeout(15.0, connect=5.0),
            follow_redirects=True,
        )
        self._delay = delay
        self._attempts = attempts
        self._backoff = backoff
        self._max_requests = max_requests
        self._jitter = jitter
        self._sleep = sleep
        self._rng = rng
        self._requests = 0  # calls to get(): the pause between pages is counted from these
        self.sent = 0  # HTTP requests actually sent, retries included: what the budget limits

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

    @property
    def remaining(self) -> int | None:
        """Requests left in this run's budget; None when there is no limit."""
        if self._max_requests is None:
            return None
        return max(0, self._max_requests - self.sent)

    def get(self, url: str) -> str:
        self._check_budget()
        if self._requests and self._delay:
            # Jitter keeps the pauses from being a perfectly regular beat on the school's server.
            self._sleep(self._delay * (1 + self._jitter * self._rng()))
        self._requests += 1

        last: Exception | None = None
        for attempt in range(1, self._attempts + 1):
            self._check_budget()
            self.sent += 1
            retry_after: float | None = None
            try:
                resp = self._client.get(url)
            except httpx2.TransportError as exc:  # timeouts, resets, DNS, ...
                last = exc
            else:
                if resp.status_code == 200:
                    resp.encoding = "utf-8"
                    return resp.text
                if resp.status_code not in RETRY_STATUS:
                    raise FetchError(f"{url} -> HTTP {resp.status_code}")
                last = FetchError(f"{url} -> HTTP {resp.status_code}")
                retry_after = retry_after_seconds(resp.headers.get("Retry-After"))
                if retry_after is not None and retry_after > MAX_RETRY_AFTER:
                    raise FetchError(
                        f"{url} -> HTTP {resp.status_code}, asked us to wait {retry_after:.0f}s"
                    ) from last
            if attempt < self._attempts:
                wait = self._backoff * 2 ** (attempt - 1)
                if retry_after is not None:
                    wait = max(wait, retry_after)  # never sooner than the server asked
                logger.warning("fetch failed (%s); retry %d in %.0fs", last, attempt, wait)
                self._sleep(wait)
        raise FetchError(f"{url}: gave up after {self._attempts} attempts ({last})") from last

    @contextmanager
    def budget(self, requests: int | None) -> Iterator[None]:
        """Allow at most `requests` more requests inside the block (None = no extra limit).

        The run's own cap (`max_requests`) still applies: the tighter of the two wins. This is how
        the list pages and the view pages of one run get separate allowances while sharing the
        same fetcher, pause and overall cap.
        """
        outer = self._max_requests
        if requests is not None:
            inner = self.sent + requests
            self._max_requests = inner if outer is None else min(outer, inner)
        try:
            yield
        finally:
            self._max_requests = outer

    def _check_budget(self) -> None:
        if self._max_requests is not None and self.sent >= self._max_requests:
            raise RequestBudgetExceeded(f"request budget of {self._max_requests} used up")

    def board(self, list_url: str) -> BoardFetcher:
        return BoardFetcher(self, list_url)

    def list_page(self, offset: int, list_url: str = BOARD_URL) -> str:
        return self.get(list_page_url(offset, list_url=list_url))

    def detail_page(self, article_no: int, list_url: str = BOARD_URL) -> str:
        return self.get(article_url(article_no, list_url))
