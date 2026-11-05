"""The board registry: a typo here would only show up as a broken crawl, so check it as data."""

from urllib.parse import urlparse

from app.crawler.parser import BOARD_URL
from app.crawler.sources import DEPARTMENTS, LEVELS, SOURCES, SourceDef


def test_slugs_and_department_codes_are_unique():
    slugs = [s.slug for s in SOURCES]
    codes = [d.code for d in DEPARTMENTS]
    assert len(slugs) == len(set(slugs))
    assert len(codes) == len(set(codes))


def test_every_source_belongs_to_a_known_department():
    assert {s.department for s in SOURCES} <= {d.code for d in DEPARTMENTS}


def test_every_department_has_a_board():
    assert {d.code for d in DEPARTMENTS} <= {s.department for s in SOURCES}


def test_levels_are_known_and_fit_the_columns():
    assert all(s.level in LEVELS for s in SOURCES)
    assert all(len(s.slug) <= 48 and len(s.name) <= 64 for s in SOURCES)
    assert all(len(d.code) <= 32 and len(d.name) <= 64 for d in DEPARTMENTS)


def test_urls_are_plain_https_list_pages():
    """No query string (the crawler adds it) and no two sources on the same page."""
    for s in SOURCES:
        parts = urlparse(s.list_url)
        assert parts.scheme == "https" and parts.netloc.endswith("skku.edu"), s.slug
        assert not parts.query and not parts.fragment, s.slug
        assert parts.path.endswith(".do"), s.slug
    assert len({s.list_url for s in SOURCES}) == len(SOURCES)


def test_the_campus_board_comes_first_with_its_original_url():
    first = SOURCES[0]
    assert (first.slug, first.list_url, first.level) == ("skku", BOARD_URL, "all")


def test_graduate_boards_are_not_crawled():
    assert SourceDef("x", "skku", "x", "https://a.skku.edu/n.do", level="grad").enabled is False
    assert SourceDef("x", "skku", "x", "https://a.skku.edu/n.do", level="undergrad").enabled
    assert SourceDef("x", "skku", "x", "https://a.skku.edu/n.do", level="all").enabled
