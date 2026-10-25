import httpx
import pytest

from app.crawler.fetch import Fetcher, FetchError


def make(handler, **kw):
    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Fetcher(client=client, sleep=sleeps.append, **kw), sleeps


def test_returns_text_as_utf8():
    f, _ = make(lambda r: httpx.Response(200, content="공지".encode()))
    assert f.get("https://x/") == "공지"


def test_retries_5xx_then_succeeds_with_backoff():
    calls = iter([503, 502, 200])
    f, sleeps = make(lambda r: httpx.Response(next(calls), text="ok"), delay=0)
    assert f.get("https://x/") == "ok"
    assert sleeps == [2.0, 4.0]


def test_gives_up_after_attempts():
    f, _ = make(lambda r: httpx.Response(503), delay=0, attempts=3)
    with pytest.raises(FetchError, match="gave up after 3"):
        f.get("https://x/")


def test_retries_transport_errors():
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] < 2:
            raise httpx.ConnectTimeout("slow", request=request)
        return httpx.Response(200, text="ok")

    f, _ = make(handler, delay=0)
    assert f.get("https://x/") == "ok"


def test_404_is_not_retried():
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        return httpx.Response(404)

    f, sleeps = make(handler, delay=0)
    with pytest.raises(FetchError, match="HTTP 404"):
        f.get("https://x/")
    assert state["n"] == 1 and sleeps == []


def test_delay_between_requests_but_not_before_the_first():
    f, sleeps = make(lambda r: httpx.Response(200, text="ok"), delay=1.5)
    f.get("https://x/a")
    assert sleeps == []
    f.get("https://x/b")
    assert sleeps == [1.5]


def test_list_page_requests_the_offset_url():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, text="ok")

    f, _ = make(handler)
    f.list_page(20)
    assert seen == [
        "https://www.skku.edu/skku/campus/skk_comm/notice01.do"
        "?mode=list&articleLimit=10&article.offset=20"
    ]
