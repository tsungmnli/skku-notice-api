from datetime import datetime
from pathlib import Path

import pytest

from app.crawl import main
from app.crawler.run import CrawlError, fetch_articles, with_deadlines
from app.crawler.store import merge_duplicates
from app.timeutil import KST

FIXTURE = (Path(__file__).parent / "fixtures" / "notice01_list.html").read_text(encoding="utf-8")
EMPTY = '<ul class="board-list-wrap"></ul>'


class FakeSource:
    def __init__(self, pages: dict[int, str]):
        self.pages = pages
        self.requested: list[int] = []

    def list_page(self, offset: int) -> str:
        self.requested.append(offset)
        return self.pages.get(offset, EMPTY)


def test_stops_at_first_empty_page():
    src = FakeSource({0: FIXTURE})
    items = fetch_articles(src, max_pages=5)
    assert len(items) == 15
    assert src.requested == [0, 10]


def test_respects_max_pages():
    src = FakeSource({0: FIXTURE, 10: FIXTURE, 20: FIXTURE})
    fetch_articles(src, max_pages=2)
    assert src.requested == [0, 10]


def test_empty_first_page_is_an_error():
    with pytest.raises(CrawlError):
        fetch_articles(FakeSource({}), max_pages=3)


def test_same_article_on_two_pages_is_merged():
    src = FakeSource({0: FIXTURE, 10: FIXTURE})
    assert len(fetch_articles(src, max_pages=2)) == 15


def test_merge_keeps_pinned_and_display_no():
    items = fetch_articles(FakeSource({0: FIXTURE}), max_pages=1)
    pinned = next(i for i in items if i.is_pinned)
    plain = type(pinned)(**{**pinned.__dict__, "is_pinned": False, "display_no": 99})
    (merged,) = merge_duplicates([pinned, plain])
    assert merged.is_pinned is True and merged.display_no == 99


def test_deadlines_from_titles():
    items = fetch_articles(FakeSource({0: FIXTURE}), max_pages=1)
    got = {n: d for n, d in with_deadlines(items).items() if d}
    assert got == {
        140492: datetime(2026, 12, 18, 23, 59, tzinfo=KST),
        140763: datetime(2026, 11, 14, 23, 59, tzinfo=KST),
        140758: datetime(2026, 10, 31, 23, 59, tzinfo=KST),
        140674: datetime(2026, 10, 26, 11, 0, tzinfo=KST),
    }


def test_cli_dry_run_prints_and_needs_no_database(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("app.crawl.Fetcher", lambda **kw: _Ctx(FakeSource({0: FIXTURE})))
    assert main(["--dry-run", "--pages", "2"]) == 0
    out = capsys.readouterr().out
    assert "140767" in out and "2026-10-31T23:59:00+09:00" in out
    assert out.count("PIN") == 5


def test_cli_returns_1_when_the_board_looks_broken(monkeypatch):
    monkeypatch.setattr("app.crawl.Fetcher", lambda **kw: _Ctx(FakeSource({})))
    assert main(["--dry-run"]) == 1


class _Ctx:
    def __init__(self, source):
        self.source = source

    def __enter__(self):
        return self.source

    def __exit__(self, *exc):
        return None
