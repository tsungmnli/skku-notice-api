from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.crawl import main
from app.crawler.config import get_crawler_settings
from app.crawler.fetch import FetchError, RequestBudgetExceeded
from app.crawler.parser import BOARD_URL, ParseError, parse_list
from app.crawler.run import (
    Board,
    BoardFailure,
    CrawlError,
    UnknownSource,
    fetch_articles,
    registry_boards,
    run_each,
    with_deadlines,
)
from app.crawler.sources import SOURCES
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


class FakeFetcher:
    """What the CLI takes from `Fetcher`: `board(url)` for one board's pages, and `budget(n)`."""

    def __init__(self, empty: set[str] | None = None):
        self.empty = empty or set()  # boards whose first list page has nothing (a broken board)
        self.boards: list[str] = []
        self.budgets: list[int | None] = []

    def board(self, list_url: str) -> FakeSource:
        self.boards.append(list_url)
        return FakeSource({} if list_url in self.empty else {0: FIXTURE})

    @contextmanager
    def budget(self, requests):
        self.budgets.append(requests)
        yield


class _Ctx:
    def __init__(self, fetcher):
        self.fetcher = fetcher

    def __enter__(self):
        return self.fetcher

    def __exit__(self, *exc):
        return None


def use_fetcher(monkeypatch, fetcher: FakeFetcher) -> FakeFetcher:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("app.crawl.Fetcher", lambda **kw: _Ctx(fetcher))
    return fetcher


def test_cli_dry_run_prints_and_needs_no_database(monkeypatch, capsys):
    use_fetcher(monkeypatch, FakeFetcher())
    assert main(["--dry-run", "--pages", "2", "--source", "skku"]) == 0
    out = capsys.readouterr().out
    assert "140767" in out and "2026-10-31T23:59:00+09:00" in out
    assert out.count("PIN") == 5


def test_cli_returns_1_when_the_board_looks_broken(monkeypatch):
    use_fetcher(monkeypatch, FakeFetcher(empty={BOARD_URL}))
    assert main(["--dry-run", "--source", "skku"]) == 1


def test_dry_run_reads_every_enabled_board_of_the_registry(monkeypatch, capsys):
    fetcher = use_fetcher(monkeypatch, FakeFetcher())
    assert main(["--dry-run"]) == 0
    enabled = [s for s in SOURCES if s.enabled]
    assert fetcher.boards == [s.list_url for s in enabled]
    slugs = {line.split()[0] for line in capsys.readouterr().out.splitlines()}
    assert slugs == {s.slug for s in enabled}


def test_source_limits_a_run_to_one_board(monkeypatch):
    fetcher = use_fetcher(monkeypatch, FakeFetcher())
    assert main(["--dry-run", "--source", "cse"]) == 0
    assert fetcher.boards == ["https://cse.skku.edu/cse/notice.do"]


def test_an_unknown_source_is_a_usage_error_and_reads_nothing(monkeypatch):
    fetcher = use_fetcher(monkeypatch, FakeFetcher())
    assert main(["--dry-run", "--source", "nope"]) == 2
    assert fetcher.boards == []


def test_a_broken_board_does_not_stop_the_others_but_fails_the_run(monkeypatch, capsys):
    sw = "https://sw.skku.edu/sw/notice.do"
    fetcher = use_fetcher(monkeypatch, FakeFetcher(empty={sw}))
    assert main(["--dry-run"]) == 1
    assert len(fetcher.boards) == len([s for s in SOURCES if s.enabled])  # sw was not the last
    slugs = {line.split()[0] for line in capsys.readouterr().out.splitlines()}
    assert "sw" not in slugs and {"skku", "cse", "ai"} <= slugs


def test_the_list_pages_get_their_own_request_budget(monkeypatch):
    fetcher = use_fetcher(monkeypatch, FakeFetcher())
    main(["--dry-run", "--source", "skku"])
    assert fetcher.budgets == [get_crawler_settings().crawl_list_requests]


def boards_named(*slugs: str) -> list[Board]:
    return [Board(i, s, f"https://x/{s}") for i, s in enumerate(slugs, start=1)]


def test_run_each_goes_on_after_a_board_fails():
    def step(board):
        if board.slug == "b":
            raise FetchError("down")
        return board.slug.upper()

    done, failed = run_each(boards_named("a", "b", "c"), step)
    assert [(b.slug, r) for b, r in done] == [("a", "A"), ("c", "C")]
    assert failed == [BoardFailure("b", "down")]


@pytest.mark.parametrize(
    "error", [FetchError("x"), ParseError("x"), CrawlError("x"), SQLAlchemyError("x")]
)
def test_run_each_counts_these_errors_as_the_boards_failure(error):
    def step(board):
        raise error

    done, failed = run_each(boards_named("a"), step)
    assert done == [] and [f.slug for f in failed] == ["a"]


def test_run_each_skips_the_rest_when_the_budget_is_gone():
    def step(board):
        if board.slug == "b":
            raise RequestBudgetExceeded("request budget of 60 used up")
        return 1

    done, failed = run_each(boards_named("a", "b", "c", "d"), step)
    assert [b.slug for b, _ in done] == ["a"]
    assert [f.slug for f in failed] == ["b", "c", "d"]
    assert all(f.reason.startswith("skipped") for f in failed[1:])


def test_run_each_does_not_hide_a_bug():
    def step(board):
        raise KeyError("a bug, not a board problem")

    with pytest.raises(KeyError):
        run_each(boards_named("a"), step)


def test_the_registry_boards_are_the_enabled_ones_in_order():
    assert [b.slug for b in registry_boards()] == [s.slug for s in SOURCES if s.enabled]
    assert registry_boards("ai")[0].list_url == "https://ai.skku.edu/ai/notice.do"


def test_an_unknown_registry_board_is_refused():
    with pytest.raises(UnknownSource):
        registry_boards("nope")


PAGE_NOS = [a.article_no for a in parse_list(FIXTURE)]
PINNED_NOS = {a.article_no for a in parse_list(FIXTURE) if a.is_pinned}


def test_incremental_stops_after_a_page_with_nothing_new():
    src = FakeSource({0: FIXTURE, 10: FIXTURE, 20: FIXTURE})
    items = fetch_articles(src, max_pages=5, known=lambda nos: set(nos))
    assert src.requested == [0]
    assert len(items) == 15


def test_incremental_goes_on_while_a_page_has_something_new():
    src = FakeSource({0: FIXTURE, 10: FIXTURE, 20: FIXTURE})
    answers = iter([set(PAGE_NOS[1:]), set(PAGE_NOS[1:]), set(PAGE_NOS)])
    fetch_articles(src, max_pages=5, known=lambda nos: next(answers))
    assert src.requested == [0, 10, 20]  # the third page is the first with nothing new


def test_incremental_is_still_capped_by_max_pages():
    src = FakeSource({0: FIXTURE, 10: FIXTURE, 20: FIXTURE})
    fetch_articles(src, max_pages=2, known=lambda nos: set())
    assert src.requested == [0, 10]


def test_a_new_pinned_notice_keeps_the_walk_going():
    src = FakeSource({0: FIXTURE, 10: FIXTURE})
    everything_but_pinned = set(PAGE_NOS) - PINNED_NOS
    fetch_articles(src, max_pages=2, known=lambda nos: everything_but_pinned & set(nos))
    assert src.requested == [0, 10]


def test_the_lookup_gets_the_numbers_of_the_page_just_read():
    seen: list[list[int]] = []
    src = FakeSource({0: FIXTURE})

    def known(nos):
        seen.append(list(nos))
        return set(nos)

    fetch_articles(src, max_pages=3, known=known)
    assert seen == [PAGE_NOS]


def test_an_empty_first_page_is_still_an_error_when_incremental():
    with pytest.raises(CrawlError):
        fetch_articles(FakeSource({}), max_pages=3, known=lambda nos: set(nos))
