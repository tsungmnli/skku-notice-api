from dataclasses import replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.crawler.sources import DEPARTMENTS, SOURCES, sync_sources
from app.models import Department, Source


@pytest.fixture
def factory(engine):
    return sessionmaker(engine, expire_on_commit=False)


def sync(factory, **kwargs):
    with factory() as session, session.begin():
        return sync_sources(session, **kwargs)


def sources_by_slug(factory) -> dict[str, Source]:
    with factory() as session:
        return {s.slug: s for s in session.scalars(select(Source))}


def test_sync_fills_departments_and_sources(factory):
    result = sync(factory)
    assert (result.departments, result.sources) == (len(DEPARTMENTS), len(SOURCES))
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Department)) == len(DEPARTMENTS)
        assert session.scalar(select(func.count()).select_from(Source)) == len(SOURCES)


def test_the_campus_board_keeps_id_1(factory):
    """It was inserted by migration 0006; sync must match it by slug, not add a second row."""
    sync(factory)
    assert sources_by_slug(factory)["skku"].id == 1


def test_sync_twice_changes_nothing(factory):
    sync(factory)
    before = {s.slug: (s.id, s.name, s.list_url) for s in sources_by_slug(factory).values()}
    sync(factory)
    after = {s.slug: (s.id, s.name, s.list_url) for s in sources_by_slug(factory).values()}
    assert before == after


def test_sort_order_follows_the_registry(factory):
    sync(factory)
    with factory() as session:
        slugs = list(session.scalars(select(Source.slug).order_by(Source.sort_order)))
    assert slugs == [s.slug for s in SOURCES]


def test_sync_updates_a_changed_board_and_never_deletes(factory):
    sync(factory)
    moved = tuple(
        replace(s, list_url="https://cse.skku.edu/cse/new.do") if s.slug == "cse" else s
        for s in SOURCES
    )
    sync(factory, sources=moved[:3])  # the rest are no longer listed
    by_slug = sources_by_slug(factory)
    assert by_slug["cse"].list_url == "https://cse.skku.edu/cse/new.do"
    assert len(by_slug) == len(SOURCES)


def test_a_graduate_board_is_stored_but_disabled(factory):
    grad = replace(SOURCES[1], slug="sw-grad", level="grad")
    sync(factory, sources=(*SOURCES, grad))
    assert sources_by_slug(factory)["sw-grad"].enabled is False
    assert sources_by_slug(factory)["sw"].enabled is True
