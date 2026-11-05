from datetime import UTC, datetime

import httpx2
import pytest

from app.crawler.fetch import Fetcher, FetchError, RequestBudgetExceeded, retry_after_seconds


def make(handler, **kw):
    sleeps: list[float] = []
    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    return Fetcher(client=client, sleep=sleeps.append, **kw), sleeps


def test_returns_text_as_utf8():
    f, _ = make(lambda r: httpx2.Response(200, content="공지".encode()))
    assert f.get("https://x/") == "공지"


def test_retries_5xx_then_succeeds_with_backoff():
    calls = iter([503, 502, 200])
    f, sleeps = make(lambda r: httpx2.Response(next(calls), text="ok"), delay=0)
    assert f.get("https://x/") == "ok"
    assert sleeps == [2.0, 4.0]


def test_gives_up_after_attempts():
    f, _ = make(lambda r: httpx2.Response(503), delay=0, attempts=3)
    with pytest.raises(FetchError, match="gave up after 3"):
        f.get("https://x/")


def test_retries_transport_errors():
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] < 2:
            raise httpx2.ConnectTimeout("slow", request=request)
        return httpx2.Response(200, text="ok")

    f, _ = make(handler, delay=0)
    assert f.get("https://x/") == "ok"


def test_404_is_not_retried():
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        return httpx2.Response(404)

    f, sleeps = make(handler, delay=0)
    with pytest.raises(FetchError, match="HTTP 404"):
        f.get("https://x/")
    assert state["n"] == 1 and sleeps == []


def test_delay_between_requests_but_not_before_the_first():
    f, sleeps = make(lambda r: httpx2.Response(200, text="ok"), delay=1.5)
    f.get("https://x/a")
    assert sleeps == []
    f.get("https://x/b")
    assert sleeps == [1.5]


def test_list_page_requests_the_offset_url():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx2.Response(200, text="ok")

    f, _ = make(handler)
    f.list_page(20)
    assert seen == [
        "https://www.skku.edu/skku/campus/skk_comm/notice01.do"
        "?mode=list&articleLimit=10&article.offset=20"
    ]


def test_detail_page_requests_the_view_url():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx2.Response(200, text="ok")

    f, _ = make(handler)
    f.detail_page(141131)
    assert seen == [
        "https://www.skku.edu/skku/campus/skk_comm/notice01.do?mode=view&articleNo=141131"
    ]


def test_waits_for_retry_after_when_it_is_longer_than_the_backoff():
    replies = iter([httpx2.Response(429, headers={"Retry-After": "7"}), httpx2.Response(200)])
    f, sleeps = make(lambda r: next(replies), delay=0)
    f.get("https://x/")
    assert sleeps == [7.0]


def test_a_short_retry_after_does_not_shorten_the_backoff():
    replies = iter([httpx2.Response(503, headers={"Retry-After": "1"}), httpx2.Response(200)])
    f, sleeps = make(lambda r: next(replies), delay=0)
    f.get("https://x/")
    assert sleeps == [2.0]


def test_a_very_long_retry_after_gives_up_at_once():
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        return httpx2.Response(429, headers={"Retry-After": "3600"})

    f, sleeps = make(handler, delay=0)
    with pytest.raises(FetchError, match="asked us to wait 3600s"):
        f.get("https://x/")
    assert state["n"] == 1 and sleeps == []


def test_retry_after_seconds_reads_numbers_and_http_dates():
    now = lambda: datetime(2026, 10, 21, 7, 27, 50, tzinfo=UTC)  # noqa: E731
    assert retry_after_seconds("5") == 5.0
    assert retry_after_seconds(None) is None
    assert retry_after_seconds("soon") is None
    assert retry_after_seconds("Wed, 21 Oct 2026 07:28:00 GMT", now=now) == 10.0
    assert retry_after_seconds("Wed, 21 Oct 2026 07:20:00 GMT", now=now) == 0.0


def test_the_budget_counts_every_request_including_retries():
    f, _ = make(lambda r: httpx2.Response(503), delay=0, attempts=3, max_requests=4)
    with pytest.raises(FetchError, match="gave up after 3"):
        f.get("https://x/a")
    assert (f.sent, f.remaining) == (3, 1)
    with pytest.raises(RequestBudgetExceeded):  # a fourth attempt is allowed, the fifth is not
        f.get("https://x/b")
    assert (f.sent, f.remaining) == (4, 0)
    with pytest.raises(RequestBudgetExceeded):
        f.get("https://x/c")
    assert f.sent == 4  # nothing was sent once the budget was gone


def test_an_exhausted_budget_does_not_even_pause():
    f, sleeps = make(lambda r: httpx2.Response(200, text="ok"), delay=1.0, max_requests=1)
    f.get("https://x/a")
    with pytest.raises(RequestBudgetExceeded):
        f.get("https://x/b")
    assert sleeps == []


def test_no_budget_means_no_limit():
    f, _ = make(lambda r: httpx2.Response(200, text="ok"), delay=0)
    for _ in range(300):
        f.get("https://x/")
    assert f.remaining is None


def test_jitter_stretches_the_pause_between_pages():
    f, sleeps = make(
        lambda r: httpx2.Response(200, text="ok"), delay=2.0, jitter=0.5, rng=lambda: 0.5
    )
    f.get("https://x/a")
    f.get("https://x/b")
    assert sleeps == [2.5]


def test_pages_of_another_board_use_its_address():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx2.Response(200, text="ok")

    cse = "https://cse.skku.edu/cse/notice.do"
    f, _ = make(handler)
    f.list_page(10, cse)
    f.detail_page(7, cse)
    assert seen == [
        f"{cse}?mode=list&articleLimit=10&article.offset=10",
        f"{cse}?mode=view&articleNo=7",
    ]


def test_a_bound_board_shares_the_fetchers_delay_and_budget():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx2.Response(200, text="ok")

    sw = "https://sw.skku.edu/sw/notice.do"
    cse = "https://cse.skku.edu/cse/notice.do"
    f, sleeps = make(handler, delay=1.5, max_requests=2)
    f.board(sw).list_page(0)
    f.board(cse).list_page(0)
    assert [u.split("/")[2] for u in seen] == ["sw.skku.edu", "cse.skku.edu"]
    assert sleeps == [1.5]  # one pause, between the two boards' requests
    with pytest.raises(RequestBudgetExceeded):
        f.board(sw).detail_page(1)


def test_a_budget_block_caps_the_requests_inside_it():
    f, _ = make(lambda r: httpx2.Response(200, text="ok"), delay=0)
    with f.budget(2):
        f.get("https://x/a")
        f.get("https://x/b")
        with pytest.raises(RequestBudgetExceeded):
            f.get("https://x/c")
    f.get("https://x/d")  # the allowance ended with the block
    assert f.sent == 3
    assert f.remaining is None


def test_a_budget_block_counts_from_where_the_run_already_is():
    f, _ = make(lambda r: httpx2.Response(200, text="ok"), delay=0, max_requests=10)
    f.get("https://x/a")
    f.get("https://x/b")
    with f.budget(2):
        f.get("https://x/c")
        f.get("https://x/d")
        with pytest.raises(RequestBudgetExceeded):
            f.get("https://x/e")
    assert f.remaining == 6  # the run's own cap is back: 10 - 4 sent


def test_a_budget_block_never_loosens_the_runs_cap():
    f, _ = make(lambda r: httpx2.Response(200, text="ok"), delay=0, max_requests=3)
    with f.budget(10):
        for page in "abc":
            f.get(f"https://x/{page}")
        with pytest.raises(RequestBudgetExceeded):
            f.get("https://x/d")
    assert f.remaining == 0


def test_the_cap_comes_back_even_when_the_block_raises():
    f, _ = make(lambda r: httpx2.Response(200, text="ok"), delay=0, max_requests=10)
    with pytest.raises(RuntimeError):
        with f.budget(1):
            raise RuntimeError("boom")
    assert f.remaining == 10


def test_retries_inside_a_budget_block_count_against_it():
    f, _ = make(lambda r: httpx2.Response(503), delay=0, attempts=3)
    with f.budget(2):
        with pytest.raises(RequestBudgetExceeded):  # 2 attempts sent, the third is refused
            f.get("https://x/a")
    assert f.sent == 2
