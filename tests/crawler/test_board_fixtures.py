"""Each department's list page, as saved from the live site (only the list itself is kept).

The same parser must read all of them. If a department redesigns its page, refresh that board's
file in fixtures/boards/ and this test shows what changed.
"""

from pathlib import Path

import pytest

from app.crawler.categories import CATEGORIES
from app.crawler.parser import parse_list
from app.crawler.sources import SOURCES

BOARDS = Path(__file__).parent / "fixtures" / "boards"
LIST_URL = {s.slug: s.list_url for s in SOURCES}

# slug -> (rows, pinned rows, article_no of the first row, has a category column)
EXPECTED = {
    "sw": (10, 0, 227620, True),
    "cse": (11, 1, 228046, True),
    "intelligentsw": (10, 0, 227950, True),
    "ai": (3, 0, 227505, True),
    "sco-notice": (16, 6, 228026, True),
    "sco-data": (10, 0, 227579, False),
    "sco-ai": (10, 0, 227578, False),
    "sco-culture": (10, 0, 205963, False),
    "sco-magnetic": (10, 0, 227140, False),
}


def load(slug: str):
    html = (BOARDS / f"{slug}_list.html").read_text(encoding="utf-8")
    return parse_list(html, LIST_URL[slug])


def test_every_registered_board_has_a_fixture_except_the_campus_one():
    assert set(EXPECTED) == {s.slug for s in SOURCES} - {"skku"}


@pytest.mark.parametrize("slug", EXPECTED)
def test_the_board_parses(slug):
    rows, pinned, first, _ = EXPECTED[slug]
    got = load(slug)
    assert len(got) == rows
    assert sum(r.is_pinned for r in got) == pinned
    assert got[0].article_no == first
    assert len({r.article_no for r in got}) == rows  # no number twice on one page


@pytest.mark.parametrize("slug", EXPECTED)
def test_rows_link_to_their_own_board(slug):
    for r in load(slug):
        assert r.url == f"{LIST_URL[slug]}?mode=view&articleNo={r.article_no}"


@pytest.mark.parametrize("slug", EXPECTED)
def test_every_row_has_a_known_category_code(slug):
    codes = {code for code, _ in CATEGORIES}
    assert all(r.category_code in codes for r in load(slug))


@pytest.mark.parametrize("slug", [s for s, e in EXPECTED.items() if not e[3]])
def test_boards_without_a_category_column_fall_back_to_general(slug):
    assert {(r.category_label, r.category_code) for r in load(slug)} == {(None, "general")}


@pytest.mark.parametrize("slug", EXPECTED)
def test_dates_and_titles_are_read(slug):
    got = load(slug)
    assert all(r.title and r.writer for r in got)
    # A pinned post stays on top however old it is (sco-notice has ones from 2022 and 2023), so
    # only the rest must be recent; the loose bound still catches a date read as garbage.
    assert all(r.posted_on.year >= 2025 for r in got if not r.is_pinned)
    assert all(r.posted_on.year >= 2000 for r in got)
