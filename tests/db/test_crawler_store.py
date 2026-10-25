from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.crawler.run import crawl
from app.crawler.store import upsert_categories
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


def test_changed_view_count_and_category_are_updated(factory):
    run(factory)
    changed = FIXTURE.replace(">861<", ">999<")
    # Move article 140767 from 행사/세미나 to 장학 (the label sits just before its link).
    head, tail = changed.split("articleNo=140767", 1)
    head = (
        head[: head.rindex("[행사/세미나]")] + "[장학]" + head[head.rindex("[행사/세미나]") + 8 :]
    )
    changed = head + "articleNo=140767" + tail
    result, _ = run(factory, changed)
    assert result.updated == 1 and result.unchanged == 14
    with Session(factory.kw["bind"]) as s:
        a = s.scalar(select(Article).where(Article.article_no == 140767))
        assert a.view_count == 999
        assert [c.code for c in a.categories] == ["scholarship"]
        assert s.scalar(select(func.count()).select_from(article_categories)) == 15


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
