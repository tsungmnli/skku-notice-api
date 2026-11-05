from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.crawler.run import crawl
from app.crawler.store import known_article_nos, upsert_categories
from app.models import Article, Category, article_categories
from app.timeutil import KST

FIXTURE = (Path(__file__).parent.parent / "crawler" / "fixtures" / "notice01_list.html").read_text(
    encoding="utf-8"
)
EMPTY = '<ul class="board-list-wrap"></ul>'


class Source:
    def __init__(self, first: str):
        self.first = first

    def list_page(self, offset: int) -> str:
        return self.first if offset == 0 else EMPTY


@pytest.fixture
def factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


def run(factory, html=FIXTURE):
    return crawl(factory, Source(html), max_pages=2)


def test_first_run_inserts_everything(factory):
    result, seen = run(factory)
    assert (seen, result.inserted, result.updated, result.unchanged) == (15, 15, 0, 0)
    with Session(factory.kw["bind"]) as s:
        assert s.scalar(select(func.count()).select_from(Category)) == 7
        assert s.scalar(select(func.count()).select_from(Article)) == 15
        assert s.scalar(select(func.count()).select_from(article_categories)) == 15
        a = s.scalar(select(Article).where(Article.article_no == 140758))
        assert a.deadline_at == datetime(2026, 10, 31, 23, 59, tzinfo=KST)
        assert [c.code for c in a.categories] == ["general"]
        assert a.url.endswith("notice01.do?mode=view&articleNo=140758")
        assert s.scalar(select(func.count()).select_from(Article).where(Article.is_pinned)) == 5


def test_second_run_changes_nothing(factory):
    run(factory)
    with Session(factory.kw["bind"]) as s:
        before = dict(s.execute(select(Article.article_no, Article.updated_at)).all())
    result, _ = run(factory)
    assert (result.inserted, result.updated, result.unchanged) == (0, 0, 15)
    with Session(factory.kw["bind"]) as s:
        after = dict(s.execute(select(Article.article_no, Article.updated_at)).all())
    assert before == after


def article(factory, article_no: int) -> Article:
    with Session(factory.kw["bind"]) as s:
        return s.scalar(select(Article).where(Article.article_no == article_no))


def backdate(factory, *, days: int) -> None:
    """Pretend every row was last written `days` ago (view refresh and updated_at)."""
    with Session(factory.kw["bind"]) as s, s.begin():
        s.execute(
            text(
                "UPDATE articles SET view_count_synced_at = now() - make_interval(days => :d), "
                "updated_at = now() - make_interval(days => :d)"
            ),
            {"d": days},
        )


def test_new_articles_have_no_content_changed_at(factory):
    run(factory)
    with Session(factory.kw["bind"]) as s:
        n = s.scalar(select(func.count()).where(Article.content_changed_at.is_not(None)))
    assert n == 0


def test_views_alone_are_not_rewritten_within_a_day(factory):
    run(factory)
    result, _ = run(factory, FIXTURE.replace(">861<", ">999<"))
    assert (result.updated, result.unchanged) == (0, 15)
    assert article(factory, 140767).view_count == 861


def test_views_are_refreshed_after_a_day_and_nothing_else_moves(factory):
    run(factory)
    backdate(factory, days=2)
    before = article(factory, 140767)
    result, _ = run(factory, FIXTURE.replace(">861<", ">999<"))
    assert (result.updated, result.unchanged) == (1, 14)
    after = article(factory, 140767)
    assert after.view_count == 999
    assert after.view_count_synced_at > before.view_count_synced_at
    # Views are not an edit: the calendar feed (updated_at) and the UPDATED badge stay put.
    assert after.updated_at == before.updated_at
    assert after.content_changed_at is None


def test_category_change_is_applied_even_when_the_row_itself_is_not_written(factory):
    run(factory)
    # Move article 140767 from 행사/세미나 to 장학 (the label sits just before its link).
    changed = FIXTURE.replace(">861<", ">999<")
    head, tail = changed.split("articleNo=140767", 1)
    head = (
        head[: head.rindex("[행사/세미나]")] + "[장학]" + head[head.rindex("[행사/세미나]") + 8 :]
    )
    changed = head + "articleNo=140767" + tail
    result, _ = run(factory, changed)
    assert result.updated == 0 and result.unchanged == 15
    with Session(factory.kw["bind"]) as s:
        a = s.scalar(select(Article).where(Article.article_no == 140767))
        assert [c.code for c in a.categories] == ["scholarship"]
        assert s.scalar(select(func.count()).select_from(article_categories)) == 15


def test_a_new_title_marks_the_post_as_changed(factory):
    old = "11월 등록 안내"
    assert FIXTURE.count(old) == 1
    run(factory)
    backdate(factory, days=2)
    before = article(factory, 140757)
    result, _ = run(factory, FIXTURE.replace(old, old + " (마감연장)"))
    assert (result.updated, result.unchanged) == (1, 14)
    after = article(factory, 140757)
    assert after.title.endswith("(마감연장)")
    assert after.content_changed_at is not None
    assert after.content_changed_at > before.view_count_synced_at
    assert after.updated_at > before.updated_at
    with Session(factory.kw["bind"]) as s:
        n = s.scalar(select(func.count()).where(Article.content_changed_at.is_not(None)))
    assert n == 1  # nobody else


def test_a_deadline_that_moves_without_a_new_title_is_not_an_edit(factory):
    """Reading rules can change a deadline while the school's title stays the same. The calendar
    feed should follow (updated_at), but the post was not edited, so no UPDATED badge."""
    run(factory)
    long_ago = datetime(2020, 1, 1, tzinfo=KST)
    with Session(factory.kw["bind"]) as s, s.begin():
        s.execute(
            text(
                "UPDATE articles SET deadline_at = '2026-01-01T00:00+09', updated_at = :t "
                "WHERE article_no = 140758"
            ),
            {"t": long_ago},
        )
    result, _ = run(factory)
    assert result.updated == 1
    a = article(factory, 140758)
    assert a.deadline_at == datetime(2026, 10, 31, 23, 59, tzinfo=KST)  # read from the title again
    assert a.updated_at > long_ago
    assert a.content_changed_at is None


def test_deadline_is_not_erased_when_the_title_loses_its_date(factory):
    run(factory)
    changed = FIXTURE.replace("(~10/31)", "")
    run(factory, changed)
    with Session(factory.kw["bind"]) as s:
        a = s.scalar(select(Article).where(Article.article_no == 140758))
        assert a.deadline_at == datetime(2026, 10, 31, 23, 59, tzinfo=KST)


def test_post_missing_from_the_pinned_section_is_unpinned(factory):
    run(factory)
    with Session(factory.kw["bind"]) as s, s.begin():
        # An old post that was pinned before and is no longer on the first page.
        s.execute(
            text(
                "INSERT INTO articles (article_no, title, writer, posted_on, is_pinned, url) "
                "VALUES (1, 'old', 'w', '2026-01-01', true, 'u')"
            )
        )
    result, _ = run(factory)
    assert result.unpinned == 1
    with Session(factory.kw["bind"]) as s:
        assert s.scalar(select(Article.is_pinned).where(Article.article_no == 1)) is False
        assert s.scalar(select(func.count()).select_from(Article).where(Article.is_pinned)) == 5


def test_categories_upsert_is_repeatable(factory):
    with factory() as s, s.begin():
        upsert_categories(s)
        upsert_categories(s)
        assert s.scalar(select(func.count()).select_from(Category)) == 7


class Recording(Source):
    def __init__(self, first: str):
        super().__init__(first)
        self.requested: list[int] = []

    def list_page(self, offset: int) -> str:
        self.requested.append(offset)
        return super().list_page(offset)


def test_known_article_nos_returns_only_the_stored_ones(factory):
    run(factory)
    with factory() as s:
        assert known_article_nos(s, [140767, 1, 2]) == {140767}
        assert known_article_nos(s, []) == set()


def test_an_incremental_run_reads_one_page_when_nothing_is_new(factory):
    run(factory)
    source = Recording(FIXTURE)
    result, seen = crawl(factory, source, max_pages=3, incremental=True)
    assert source.requested == [0]
    assert (seen, result.inserted, result.updated) == (15, 0, 0)


def test_an_incremental_run_on_an_empty_database_walks_every_page(factory):
    source = Recording(FIXTURE)
    result, _ = crawl(factory, source, max_pages=2, incremental=True)
    assert source.requested == [0, 10]
    assert result.inserted == 15


def test_an_incremental_run_stores_a_post_it_did_not_know(factory):
    run(factory)
    with factory() as s, s.begin():
        s.execute(text("DELETE FROM articles WHERE article_no = 140767"))
    source = Recording(FIXTURE)
    result, _ = crawl(factory, source, max_pages=3, incremental=True)
    assert source.requested == [0, 10]  # page 1 had something new, so page 2 was looked at
    assert result.inserted == 1
    # And the next incremental run is back to a single request.
    again = Recording(FIXTURE)
    crawl(factory, again, max_pages=3, incremental=True)
    assert again.requested == [0]
