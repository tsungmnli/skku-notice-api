"""Crawling more than one board into the same database."""

from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.crawl import crawl_boards
from app.crawler.details import read_details
from app.crawler.fetch import FetchError
from app.crawler.run import UnknownSource, crawl, load_boards
from app.crawler.sources import CAMPUS_SOURCE_ID, SOURCES, sync_sources
from app.crawler.store import known_article_nos
from app.models import Article, Source

FIXTURES = Path(__file__).parent.parent / "crawler" / "fixtures"
CAMPUS_LIST = (FIXTURES / "notice01_list.html").read_text(encoding="utf-8")
EMPTY = '<ul class="board-list-wrap"></ul>'


def board_html(slug: str) -> str:
    return (FIXTURES / "boards" / f"{slug}_list.html").read_text(encoding="utf-8")


class Pages:
    """One list page at offset 0; every later page is empty."""

    def __init__(self, first: str):
        self.first = first

    def list_page(self, offset: int) -> str:
        return self.first if offset == 0 else EMPTY


@pytest.fixture
def factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def boards(factory) -> dict[str, Source]:
    with factory() as session, session.begin():
        sync_sources(session)
    with factory() as session:
        return {s.slug: s for s in session.scalars(select(Source))}


def crawl_board(factory, board: Source, html: str, **kw):
    return crawl(
        factory,
        Pages(html),
        max_pages=2,
        source_id=board.id,
        list_url=board.list_url,
        **kw,
    )


def test_articles_are_stored_under_their_board(factory, boards):
    sw = boards["sw"]
    result, seen = crawl_board(factory, sw, board_html("sw"))
    assert (seen, result.inserted) == (10, 10)
    with Session(factory.kw["bind"]) as s:
        rows = s.scalars(select(Article)).all()
    assert {a.source_id for a in rows} == {sw.id}
    assert all(a.url.startswith(sw.list_url) for a in rows)


def test_the_default_board_is_the_campus_one(factory, boards):
    crawl(factory, Pages(CAMPUS_LIST), max_pages=2)
    with Session(factory.kw["bind"]) as s:
        assert {a.source_id for a in s.scalars(select(Article))} == {CAMPUS_SOURCE_ID}


def test_the_same_number_on_two_boards_is_two_articles(factory, boards):
    # Same post number, different boards: the page below is stored once for each.
    for slug in ("sw", "cse"):
        crawl_board(factory, boards[slug], board_html("sw"))
    with Session(factory.kw["bind"]) as s:
        rows = s.scalars(select(Article).where(Article.article_no == 227620)).all()
    assert {a.source_id for a in rows} == {boards["sw"].id, boards["cse"].id}


def test_crawling_twice_changes_nothing_on_a_department_board(factory, boards):
    crawl_board(factory, boards["cse"], board_html("cse"))
    result, _ = crawl_board(factory, boards["cse"], board_html("cse"))
    assert (result.inserted, result.updated, result.unpinned) == (0, 0, 0)


def test_one_board_never_unpins_anothers_posts(factory, boards):
    """cse has a pinned post; crawling a board with no pinned posts must leave it pinned."""
    crawl_board(factory, boards["cse"], board_html("cse"))
    crawl_board(factory, boards["ai"], board_html("ai"))
    with Session(factory.kw["bind"]) as s:
        pinned = s.scalars(
            select(Article).where(Article.source_id == boards["cse"].id, Article.is_pinned)
        ).all()
    assert len(pinned) == 1


def test_a_board_unpins_its_own_posts(factory, boards):
    crawl_board(factory, boards["cse"], board_html("cse"))
    unpinned_page = board_html("cse").replace("board-list-content-top", "x")
    crawl_board(factory, boards["cse"], unpinned_page)
    with Session(factory.kw["bind"]) as s:
        still = s.scalars(
            select(Article).where(Article.source_id == boards["cse"].id, Article.is_pinned)
        ).all()
    assert still == []


def test_known_numbers_are_per_board(factory, boards):
    crawl_board(factory, boards["sw"], board_html("sw"))
    with Session(factory.kw["bind"]) as s:
        assert known_article_nos(s, [227620], boards["sw"].id) == {227620}
        assert known_article_nos(s, [227620], boards["cse"].id) == set()
        assert known_article_nos(s, [227620]) == set()  # the campus board does not have it


def test_incremental_stops_per_board(factory, boards):
    """Page 1 of a board is all known, so the walk ends there; nothing is asked of page 2."""
    crawl_board(factory, boards["ai"], board_html("ai"))

    class Counting(Pages):
        asked: list[int] = []

        def list_page(self, offset: int) -> str:
            self.asked.append(offset)
            return super().list_page(offset)

    src = Counting(board_html("ai"))
    crawl(
        factory,
        src,
        max_pages=3,
        incremental=True,
        source_id=boards["ai"].id,
        list_url=boards["ai"].list_url,
    )
    assert src.asked == [0]


class Views:
    """Records which board each view page was requested from."""

    def __init__(self):
        self.asked: list[tuple[int, str]] = []

    def detail_page(self, article_no: int, list_url: str = "") -> str:
        self.asked.append((article_no, list_url))
        return '<dl class="board-write-box"><dd><pre>내용</pre></dd></dl>'


def test_a_view_page_is_read_from_the_articles_own_board(factory, boards):
    crawl_board(factory, boards["ai"], board_html("ai"))
    views = Views()
    read_details(factory, views, limit=50, max_age_days=3650)
    assert views.asked
    assert {url for _, url in views.asked} == {boards["ai"].list_url}


def test_a_disabled_board_is_not_read(factory, boards):
    crawl_board(factory, boards["ai"], board_html("ai"))
    with factory() as session, session.begin():
        session.execute(update(Source).where(Source.slug == "ai").values(enabled=False))
    views = Views()
    read_details(factory, views, limit=50, max_age_days=3650)
    assert views.asked == []


def test_all_registered_boards_can_be_crawled_in_turn(factory, boards):
    for src in SOURCES[1:]:
        crawl_board(factory, boards[src.slug], board_html(src.slug))
    with Session(factory.kw["bind"]) as s:
        used = {a.source_id for a in s.scalars(select(Article))}
    assert used == {boards[s.slug].id for s in SOURCES[1:]}


def test_load_boards_gives_the_enabled_ones_in_registry_order(factory, boards):
    loaded = load_boards(factory)
    assert [b.slug for b in loaded] == [s.slug for s in SOURCES if s.enabled]
    assert loaded[0].id == CAMPUS_SOURCE_ID
    assert loaded[1].list_url == "https://sw.skku.edu/sw/notice.do"


def test_load_boards_leaves_out_a_disabled_board(factory, boards):
    with factory() as session, session.begin():
        session.execute(update(Source).where(Source.slug == "ai").values(enabled=False))
    assert "ai" not in [b.slug for b in load_boards(factory)]
    with pytest.raises(UnknownSource):
        load_boards(factory, "ai")


def test_load_boards_by_slug_gives_just_that_board(factory, boards):
    (only,) = load_boards(factory, "sco-data")
    assert (only.id, only.slug) == (boards["sco-data"].id, "sco-data")


def test_load_boards_refuses_an_unknown_slug(factory, boards):
    with pytest.raises(UnknownSource):
        load_boards(factory, "nope")


def test_load_boards_before_a_sync_gives_only_the_campus_board(factory):
    assert [b.slug for b in load_boards(factory)] == ["skku"]


class Fleet:
    """Stands in for the Fetcher: a first list page per board address; any other board fails."""

    def __init__(self, pages: dict[str, str]):
        self.pages = pages

    def board(self, list_url: str) -> Pages:
        if list_url not in self.pages:
            raise FetchError(f"{list_url} -> HTTP 503")
        return Pages(self.pages[list_url])


def test_a_failing_board_does_not_stop_the_boards_after_it(factory, boards):
    wanted = [b for b in load_boards(factory) if b.slug in ("sw", "ai", "cse")]  # ai has no page
    fleet = Fleet(
        {boards["sw"].list_url: board_html("sw"), boards["cse"].list_url: board_html("cse")}
    )
    totals, seen, failed = crawl_boards(factory, fleet, wanted, pages=2, incremental=False)
    assert [f.slug for f in failed] == ["ai"]
    assert seen > 0 and totals.inserted == seen
    with Session(factory.kw["bind"]) as s:
        used = {a.source_id for a in s.scalars(select(Article))}
    assert used == {boards["sw"].id, boards["cse"].id}


def test_crawl_boards_adds_up_the_boards(factory, boards):
    wanted = [b for b in load_boards(factory) if b.slug in ("sw", "cse")]
    fleet = Fleet(
        {boards["sw"].list_url: board_html("sw"), boards["cse"].list_url: board_html("cse")}
    )
    totals, seen, failed = crawl_boards(factory, fleet, wanted, pages=2, incremental=False)
    assert failed == []
    with Session(factory.kw["bind"]) as s:
        assert len(s.scalars(select(Article)).all()) == totals.inserted == seen
    again, _, _ = crawl_boards(factory, fleet, wanted, pages=2, incremental=False)
    assert (again.inserted, again.updated) == (0, 0)


def test_read_details_can_be_limited_to_one_board(factory, boards):
    crawl_board(factory, boards["ai"], board_html("ai"))
    crawl_board(factory, boards["cse"], board_html("cse"))
    views = Views()
    read_details(factory, views, limit=50, max_age_days=3650, source_id=boards["cse"].id)
    assert views.asked
    assert {url for _, url in views.asked} == {boards["cse"].list_url}
