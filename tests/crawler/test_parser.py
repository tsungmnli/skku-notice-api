from datetime import date
from pathlib import Path

import pytest

from app.crawler.parser import ParseError, article_url, list_page_url, parse_list

FIXTURE = Path(__file__).parent / "fixtures" / "notice01_list.html"


@pytest.fixture(scope="module")
def rows():
    return parse_list(FIXTURE.read_text(encoding="utf-8"))


def by_no(rows, no):
    return next(r for r in rows if r.article_no == no)


def test_parses_every_row(rows):
    assert len(rows) == 15
    assert len({r.article_no for r in rows}) == 15


def test_pinned_rows_come_first_and_have_no_display_no(rows):
    pinned = [r for r in rows if r.is_pinned]
    assert [r.article_no for r in pinned] == [140720, 140563, 140492, 140472, 140433]
    assert rows[:5] == pinned
    assert all(r.display_no is None for r in pinned)


def test_regular_row_fields(rows):
    r = by_no(rows, 140767)
    assert r.title == "사회과학대학 제41회 수선포럼 개최 안내: 2026.11.18.(수) 11:30~13:00"
    assert (r.category_label, r.category_code) == ("행사/세미나", "event")
    assert r.writer == "사회과학연구원"
    assert r.posted_on == date(2026, 10, 24)
    assert r.display_no == 26157
    assert r.view_count == 861
    assert r.is_pinned is False
    assert r.has_attachment is True
    assert (
        r.url == "https://www.skku.edu/skku/campus/skk_comm/notice01.do?mode=view&articleNo=140767"
    )


def test_attachment_flag(rows):
    assert by_no(rows, 140758).has_attachment is False
    assert by_no(rows, 140472).has_attachment is False
    assert by_no(rows, 140720).has_attachment is True


def test_bom_is_stripped_from_title(rows):
    assert by_no(rows, 140758).title == "성균타임즈 2026 ESSAY CONTEST 개최 (~10/31)"


def test_view_count_of_pinned_row(rows):
    assert by_no(rows, 140563).view_count == 75027


def test_labels_map_to_codes(rows):
    got = {r.category_label: r.category_code for r in rows}
    assert got == {
        "일반": "general",
        "행사/세미나": "event",
        "채용/모집": "recruit",
        "장학": "scholarship",
    }


def test_missing_list_raises():
    with pytest.raises(ParseError):
        parse_list("<html><body>점검 중입니다</body></html>")


def test_empty_list_page_is_ok():
    assert parse_list('<ul class="board-list-wrap"> </ul>') == []


def test_unknown_label_falls_back_to_general(caplog):
    html = (
        '<ul class="board-list-wrap"><li><dl><dt class="board-list-content-title">'
        '<span class="c-board-list-category">[신설]</span>'
        '<a href="?mode=view&amp;articleNo=1">제목</a></dt>'
        '<dd class="board-list-content-info"><ul><li>No.1</li><li>팀</li>'
        "<li>2026-10-01</li><li>조회수<span>3</span></li></ul></dd></dl></li></ul>"
    )
    (row,) = parse_list(html)
    assert (row.category_label, row.category_code) == ("신설", "general")
    assert "unknown board category label" in caplog.text


def test_row_without_category_is_general():
    html = (
        '<ul class="board-list-wrap"><li><dl><dt class="board-list-content-title">'
        '<a href="?mode=view&amp;articleNo=2">제목</a></dt>'
        '<dd class="board-list-content-info"><ul><li>No.2</li><li>팀</li>'
        "<li>2026-10-01</li><li>조회수<span>1,234</span></li></ul></dd></dl></li></ul>"
    )
    (row,) = parse_list(html)
    assert row.category_label is None and row.category_code == "general"
    assert row.view_count == 1234


def test_url_helpers():
    assert article_url(5).endswith("notice01.do?mode=view&articleNo=5")
    assert list_page_url(20).endswith("notice01.do?mode=list&articleLimit=10&article.offset=20")
