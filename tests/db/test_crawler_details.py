from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app.crawl import main
from app.crawler.config import get_crawler_settings
from app.crawler.details import DetailResult, read_details
from app.crawler.fetch import FetchError, RequestBudgetExceeded
from app.crawler.parser import BOARD_URL
from app.crawler.run import crawl
from app.models import Article
from app.timeutil import KST

FIXTURES = Path(__file__).parent.parent / "crawler" / "fixtures"
LIST_PAGE = (FIXTURES / "notice01_list.html").read_text(encoding="utf-8")
EMPTY = '<ul class="board-list-wrap"></ul>'
TODAY = date(2026, 11, 3)

# The four real notices saved as fixtures: (article_no, title).
QUANTUM = (141131, "양자생명물리과학원 행정직원 모집")
COLLOQUIUM = (141483, "SKKU AI Colloquium 2026 행사 지원 아르바이트 모집 안내 (11/6 금)")
SUCCESS_STORY = (141051, "[학생성공센터] 대학원 학생성공스토리 공모전(11/11) 진행자 및 알바 모집")
HACKATHON = (141478, "2026년 재난안전 AI 데이터 활용 해커톤 공고 안내")
# Four sessions under one heading: stored as 11/2 15:00 by the old rules, no event date now.
SESSIONS = (140500, "(성균인성교육센터) 2026-2 성균인의 자기이해 특별프로그램 참가자 모집")


def kst(*args):
    return datetime(*args, tzinfo=KST)


def page(article_no: int) -> str:
    return (FIXTURES / f"detail_{article_no}.html").read_text(encoding="utf-8")


class Detail:
    """Serves view pages; an article_no in `fail` raises, one with no page is a parse error."""

    def __init__(self, pages: dict[int, str] | None = None, fail: set[int] = frozenset()):
        self.pages = pages or {}
        self.fail = fail
        self.requested: list[int] = []

    def detail_page(self, article_no: int, list_url: str = BOARD_URL) -> str:
        self.requested.append(article_no)
        if article_no in self.fail:
            raise FetchError(f"{article_no}: server down")
        return self.pages.get(article_no, "<html>삭제된 게시글입니다.</html>")


class ListSource:
    def __init__(self, html: str = LIST_PAGE):
        self.html = html

    def list_page(self, offset: int) -> str:
        return self.html if offset == 0 else EMPTY


@pytest.fixture
def factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


def add(factory, article_no, title="공지", posted_on=date(2026, 11, 2), **cols):
    with factory() as s, s.begin():
        s.add(
            Article(
                article_no=article_no,
                title=title,
                writer="w",
                posted_on=posted_on,
                url="u",
                **cols,
            )
        )


def row(factory, article_no) -> Article:
    with factory() as s:
        return s.scalar(select(Article).where(Article.article_no == article_no))


def read(factory, source, **kw):
    kw.setdefault("limit", 30)
    return read_details(factory, source, today=TODAY, **kw)


def test_reads_deadline_and_event_from_the_bodies(factory):
    for no, title in (QUANTUM, COLLOQUIUM, SUCCESS_STORY, HACKATHON):
        add(factory, no, title)
    source = Detail({no: page(no) for no, _ in (QUANTUM, COLLOQUIUM, SUCCESS_STORY, HACKATHON)})

    result = read(factory, source)

    assert (result.checked, result.failed, result.gave_up) == (4, 0, False)
    quantum = row(factory, QUANTUM[0])
    assert quantum.deadline_at == kst(2026, 11, 10, 23, 59)
    assert quantum.deadline_source == "body"
    assert quantum.deadline_evidence == "가. 모집기간 : 2026.10.30(금)~2026.11.10(화)까지"
    assert quantum.event_at is None
    colloquium = row(factory, COLLOQUIUM[0])
    assert colloquium.deadline_at is None and colloquium.deadline_source is None
    assert colloquium.event_at == kst(2026, 11, 6, 9, 30)  # the body's time beats the title's date
    assert row(factory, SUCCESS_STORY[0]).deadline_at == kst(2026, 11, 5, 23, 59)
    assert row(factory, SUCCESS_STORY[0]).event_at == kst(2026, 11, 11, 13, 30)  # `26. 11. 11.(수)`
    hackathon = row(factory, HACKATHON[0])  # `참가방법 : 이메일 접수(~11.12.(목) 23시 59분)`
    assert hackathon.deadline_at == kst(2026, 11, 12, 23, 59)
    assert hackathon.deadline_source == "body"
    # Nothing found is still "checked": the page is not requested again.
    assert row(factory, COLLOQUIUM[0]).detail_checked_at


def test_event_falls_back_to_the_title(factory):
    add(factory, 1, "특강 안내 (11/20(금) 오후 2시)")
    read(
        factory,
        Detail({1: '<dl class="board-write-box"><dd><pre>많은 참석 바랍니다.</pre></dd></dl>'}),
    )
    assert row(factory, 1).event_at == kst(2026, 11, 20, 14, 0)


def test_several_days_in_the_body_do_not_fall_back_to_the_title(factory):
    add(factory, 1, "특강 안내 (11/20(금) 오후 2시)")
    body = "일시 : 11/16(월) ~ 11/17(화)"
    read(factory, Detail({1: f'<dl class="board-write-box"><dd><pre>{body}</pre></dd></dl>'}))
    assert row(factory, 1).event_at is None
    assert row(factory, 1).detail_checked_at


def test_a_notice_is_read_once(factory):
    add(factory, *QUANTUM)
    source = Detail({QUANTUM[0]: page(QUANTUM[0])})
    assert read(factory, source).checked == 1
    assert read(factory, source) == DetailResult()
    assert source.requested == [QUANTUM[0]]


def test_only_notices_that_need_a_page_are_requested(factory):
    add(factory, 1, "fresh")
    add(factory, 2, "title had a deadline", deadline_at=kst(2026, 11, 9, 23, 59))
    add(factory, 3, "already read", detail_checked_at=kst(2026, 11, 2, 9, 0))
    add(factory, 4, "too old", posted_on=date(2026, 8, 1))
    source = Detail({1: "<html></html>"})
    read(factory, source, max_age_days=60)
    assert source.requested == [1]


def test_newest_first_and_limited_per_run(factory):
    add(factory, 1, posted_on=date(2026, 10, 20))
    add(factory, 2, posted_on=date(2026, 11, 2))
    add(factory, 3, posted_on=date(2026, 10, 30))
    source = Detail(
        {n: '<dl class="board-write-box"><dd><pre>x</pre></dd></dl>' for n in (1, 2, 3)}
    )
    assert read(factory, source, limit=2).checked == 2
    assert source.requested == [2, 3]
    assert read(factory, source, limit=2).checked == 1  # the rest, next run
    assert read(factory, source, limit=0) == DetailResult()


def test_failed_pages_stay_pending_for_the_next_run(factory):
    ok = '<dl class="board-write-box"><dd><pre>x</pre></dd></dl>'
    for n, day in ((1, 2), (2, 1), (3, 1)):
        add(factory, n, posted_on=date(2026, 11, day))
    # 1 downloads fine, 2 is a connection failure, 3 is a page without the body box.
    result = read(factory, Detail({1: ok, 3: "<html></html>"}, fail={2}))
    assert (result.checked, result.failed, result.gave_up) == (1, 2, False)
    assert row(factory, 1).detail_checked_at is not None
    assert row(factory, 2).detail_checked_at is None
    assert row(factory, 3).detail_checked_at is None
    # Later the school's server answers again.
    again = Detail({2: ok, 3: ok})
    assert read(factory, again).checked == 2
    assert sorted(again.requested) == [2, 3]


def test_gives_up_after_three_failures_in_a_row(factory):
    for n in range(1, 6):
        add(factory, n, posted_on=date(2026, 11, 2))
    source = Detail(fail=set(range(1, 6)))
    result = read(factory, source)
    assert (result.checked, result.failed, result.gave_up) == (0, 3, True)
    assert len(source.requested) == 3


def test_a_success_resets_the_failure_streak(factory):
    ok = '<dl class="board-write-box"><dd><pre>x</pre></dd></dl>'
    # Newest first: 6, 5 fail, 4 ok, 3, 2 fail, 1 ok.
    for n in range(1, 7):
        add(factory, n, posted_on=date(2026, 10, 20 + n))
    result = read(factory, Detail({4: ok, 1: ok}, fail={6, 5, 3, 2}))
    assert (result.checked, result.failed, result.gave_up) == (2, 4, False)


def test_a_title_deadline_replaces_a_body_deadline(factory):
    crawl(factory, ListSource(), max_pages=2)
    article = row(factory, 140758)  # "(~10/31)" in the title
    assert (article.deadline_source, article.deadline_evidence) == ("title", None)
    assert row(factory, 140767).deadline_source is None

    def make_body_deadline():
        with factory() as s, s.begin():
            s.execute(
                update(Article)
                .where(Article.article_no == 140758)
                .values(deadline_at=kst(2026, 11, 20, 23, 59), deadline_source="body")
                .values(deadline_evidence="마감 : 11/20")
            )

    make_body_deadline()
    crawl(factory, ListSource(), max_pages=2)
    article = row(factory, 140758)
    assert article.deadline_at == kst(2026, 10, 31, 23, 59)
    assert (article.deadline_source, article.deadline_evidence) == ("title", None)

    # The title losing its date does not touch a body deadline.
    make_body_deadline()
    crawl(factory, ListSource(LIST_PAGE.replace("(~10/31)", "")), max_pages=2)
    article = row(factory, 140758)
    assert article.deadline_at == kst(2026, 11, 20, 23, 59)
    assert (article.deadline_source, article.deadline_evidence) == ("body", "마감 : 11/20")


LONG_AGO = kst(2020, 1, 1, 9, 0)


def test_unchecking_a_page_reads_it_again_and_stamps_only_real_changes(factory):
    # What the old rules stored from the body; `detail_checked_at` is cleared (see README).
    for n in (1, 2, 3):
        add(
            factory,
            n,
            deadline_at=kst(2026, 11, 10, 23, 59) if n != 2 else kst(2026, 11, 20, 23, 59),
            deadline_source="body",
            deadline_evidence="old",
            updated_at=LONG_AGO,
        )
    deadline_page = '<dl class="board-write-box"><dd><pre>마감 : 11/20</pre></dd></dl>'
    no_deadline = '<dl class="board-write-box"><dd><pre>모집합니다</pre></dd></dl>'

    result = read(factory, Detail({1: deadline_page, 2: deadline_page, 3: no_deadline}))

    assert result.checked == 3
    moved, same, gone = (row(factory, n) for n in (1, 2, 3))
    assert moved.deadline_at == kst(2026, 11, 20, 23, 59)
    assert moved.deadline_evidence == "마감 : 11/20"
    assert moved.updated_at > LONG_AGO
    assert same.deadline_at == kst(2026, 11, 20, 23, 59)
    assert same.updated_at == LONG_AGO  # read again, same answer: the calendar feed stays put
    assert (gone.deadline_at, gone.deadline_source, gone.deadline_evidence) == (None, None, None)
    assert gone.updated_at > LONG_AGO


def test_a_title_deadline_is_never_read_again(factory):
    add(factory, 1, deadline_at=kst(2026, 11, 9, 23, 59), deadline_source="title")
    source = Detail({1: '<dl class="board-write-box"><dd><pre>마감 : 11/20</pre></dd></dl>'})
    assert read(factory, source) == DetailResult()
    assert source.requested == []


def test_a_failed_reread_keeps_what_was_stored(factory):
    add(factory, 1, deadline_at=kst(2026, 11, 10, 23, 59), deadline_source="body")
    result = read(factory, Detail(fail={1}))
    assert (result.checked, result.failed) == (0, 1)
    assert row(factory, 1).deadline_at == kst(2026, 11, 10, 23, 59)
    assert row(factory, 1).detail_checked_at is None  # tried again next run


def test_a_wrong_event_date_is_cleared_when_the_page_is_read_again(factory):
    add(factory, *SESSIONS, event_at=kst(2026, 11, 2, 15, 0))
    read(factory, Detail({SESSIONS[0]: page(SESSIONS[0])}))
    got = row(factory, SESSIONS[0])
    assert got.event_at is None
    assert got.detail_checked_at is not None


class FakeFetcher(ListSource):
    """The school's site: the real list page, and the same notice body for every view."""

    def detail_page(self, article_no: int, list_url: str = BOARD_URL) -> str:
        return page(QUANTUM[0])

    # The CLI takes three things from `Fetcher`: `board(url)` (one board's pages), `budget(n)`
    # (a request cap around each phase) and `detail_page` (read straight from the fetcher).
    def board(self, list_url: str) -> "FakeFetcher":
        return self  # every board serves the same list page

    @contextmanager
    def budget(self, requests):
        yield  # this fake has no request cap


class _Ctx:
    def __init__(self, source):
        self.source = source

    def __enter__(self):
        return self.source

    def __exit__(self, *exc):
        return None


def test_cli_reads_details_after_the_list(monkeypatch, migrated_db_url, factory):
    monkeypatch.setenv("DATABASE_URL", migrated_db_url)
    monkeypatch.setattr("app.crawl.Fetcher", lambda **kw: _Ctx(FakeFetcher()))
    monkeypatch.setattr("app.crawler.details.now_kst", lambda: kst(2026, 10, 24, 12, 0))
    get_crawler_settings.cache_clear()
    try:
        assert main([]) == 0
    finally:
        get_crawler_settings.cache_clear()
    with Session(factory.kw["bind"]) as s:
        sources = Counter(s.scalars(select(Article.deadline_source)))
    # 4 of the 15 titles carry a deadline; the other 11 got one from the body.
    assert sources == {"title": 4, "body": 11}


class RunsOut(Detail):
    """Serves `allowed` pages, then the request budget is gone."""

    def __init__(self, pages: dict[int, str], allowed: int):
        super().__init__(pages)
        self.allowed = allowed

    def detail_page(self, article_no: int, list_url: str = BOARD_URL) -> str:
        if len(self.requested) >= self.allowed:
            raise RequestBudgetExceeded("request budget used up")
        return super().detail_page(article_no, list_url)


def test_an_used_up_request_budget_stops_the_run_without_counting_failures(factory):
    ok = '<dl class="board-write-box"><dd><pre>x</pre></dd></dl>'
    for n in range(1, 5):  # newest first: 4, 3, 2, 1
        add(factory, n, posted_on=date(2026, 10, 20 + n))
    source = RunsOut({n: ok for n in range(1, 5)}, allowed=2)
    result = read(factory, source)
    assert (result.checked, result.failed, result.gave_up, result.budget_hit) == (2, 0, False, True)
    assert row(factory, 4).detail_checked_at is not None
    assert row(factory, 3).detail_checked_at is not None
    assert row(factory, 2).detail_checked_at is None  # waits for the next run
    assert row(factory, 1).detail_checked_at is None
