"""`GET /articles` must behave like the web app's mock (as amended in docs/api-contract.md).

Each case runs against the real API and against the plain-Python reference; ordering, totals and
paging must agree.
"""

from datetime import datetime

import pytest

from app.timeutil import KST, get_now
from tests.db import reference
from tests.db.conftest import NOW

ROWS = reference.dataset()

CASES: dict[str, dict] = {
    "default": {},
    "posted_asc": {"sort": "posted_asc"},
    "views_desc": {"sort": "views_desc"},
    "deadline_asc hides passed": {"sort": "deadline_asc"},
    "pinned only": {"pinned": "only"},
    "pinned exclude": {"pinned": "exclude", "sort": "views_desc"},
    "q one word (title)": {"q": "안내"},
    "q all words must match": {"q": "학사 안내"},
    "q extra whitespace": {"q": "  장학   신청 "},
    "q writer": {"q": "팀"},
    "q category name": {"q": "채용"},
    "q article number": {"q": "1005"},
    "q no hit": {"q": "없는단어"},
    "q percent is literal": {"q": "%"},
    "q underscore is literal": {"q": "_"},
    "category one": {"category": ["academic"]},
    "category any of": {"category": ["career", "event"]},
    "category unknown": {"category": ["nope"]},
    "writer one": {"writer": ["교무팀"]},
    "writer any of": {"writer": ["교무팀", "입학처"]},
    "posted range": {"date_from": "2026-03-01", "date_to": "2026-06-30"},
    "posted from only": {"date_from": "2026-08-01"},
    "posted to only": {"date_to": "2026-02-28"},
    "has_deadline true": {"has_deadline": True},
    "has_deadline false": {"has_deadline": False},
    "deadline range": {"deadline_from": "2026-10-01", "deadline_to": "2026-10-31"},
    "deadline from only": {"deadline_from": "2026-11-01"},
    "deadline to only (passed ones)": {"deadline_to": "2026-03-01"},
    "deadline range + deadline_asc includes passed": {
        "deadline_from": "2026-02-01",
        "deadline_to": "2026-10-31",
        "sort": "deadline_asc",
    },
    "deadline single day": {"deadline_from": "2026-10-26", "deadline_to": "2026-10-26"},
    "deadline_asc + category": {"sort": "deadline_asc", "category": ["scholarship"]},
    "combo": {
        "q": "안내",
        "category": ["academic", "admission"],
        "pinned": "exclude",
        "has_deadline": True,
        "sort": "deadline_asc",
    },
    "page 1 of 5": {"size": 5, "page": 1},
    "page 3 of 5": {"size": 5, "page": 3},
    "last page": {"size": 5, "page": 8},
    "page past the end": {"size": 5, "page": 99},
    "size above 50 is clamped": {"size": 100},
    "size below 1 is clamped": {"size": -3},
    "size 7 over views": {"size": 7, "page": 2, "sort": "views_desc"},
}


def to_params(case: dict) -> dict:
    return {k: ("true" if v is True else "false" if v is False else v) for k, v in case.items()}


def assert_matches_reference(api, case: dict, now: datetime) -> None:
    res = api.get("/api/v1/articles", params=to_params(case))
    assert res.status_code == 200, res.text
    body = res.json()
    want = reference.query(ROWS, case, now)
    assert [a["article_no"] for a in body["items"]] == want["article_nos"]
    assert {k: body[k] for k in ("total", "page", "size", "has_next")} == {
        k: want[k] for k in ("total", "page", "size", "has_next")
    }


@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_articles_match_the_reference(api, case):
    assert_matches_reference(api, case, NOW)


def test_the_cases_are_not_vacuous():
    """Guard against a parity test that passes only because everything is empty."""
    sizes = {name: reference.query(ROWS, c, NOW)["total"] for name, c in CASES.items()}
    assert sizes["default"] == 36
    assert 0 < sizes["pinned only"] < 36
    assert 0 < sizes["deadline_asc hides passed"] < sizes["has_deadline true"]
    assert sizes["q category name"] > 0
    assert sizes["q percent is literal"] == sizes["q underscore is literal"] == 0
    assert sizes["has_deadline true"] + sizes["has_deadline false"] == 36


def test_deadline_asc_keeps_the_whole_current_day(api):
    """On Oct 21 at 23:30 a deadline of 17:00 that day has passed by the clock, but its day has
    not: it stays (the cut is Seoul midnight, same as the web app's `startOf("day")`)."""
    late = datetime(2026, 10, 21, 23, 30, tzinfo=KST)
    api.app.dependency_overrides[get_now] = lambda: late
    try:
        case = {"sort": "deadline_asc", "size": 50}
        assert_matches_reference(api, case, late)
        res = api.get("/api/v1/articles", params=case).json()
        days = {a["deadline_at"][:10] for a in res["items"]}
        assert "2026-10-21" in days
        assert min(days) == "2026-10-21"
    finally:
        api.app.dependency_overrides[get_now] = lambda: NOW
