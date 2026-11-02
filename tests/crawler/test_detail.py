from datetime import date, datetime
from pathlib import Path

import pytest

from app.crawler.deadline import extract_from_body
from app.crawler.parser import ParseError, parse_detail
from app.timeutil import KST

FIXTURES = Path(__file__).parent / "fixtures"
POSTED = date(2026, 11, 2)


def body(article_no: int) -> str:
    return parse_detail((FIXTURES / f"detail_{article_no}.html").read_text(encoding="utf-8"))


def kst(*args):
    return datetime(*args, tzinfo=KST)


def test_body_keeps_line_breaks():
    # 양자생명물리과학원 행정직원 모집; indentation of the original text is kept.
    lines = [line.strip() for line in body(141131).splitlines()]
    assert lines[0].startswith("성균관대학교 양자생명물리과학원에서 근무할 직원을")
    assert "가. 모집기간 : 2026.10.30(금)~2026.11.10(화)까지" in lines
    assert lines[-1] == "ohjuyi@skku.edu, 031-299-4790"


def test_body_is_trimmed_and_has_no_trailing_spaces():
    text = body(141483)  # SKKU AI Colloquium
    assert text == text.strip()
    assert all(line == line.rstrip() for line in text.splitlines())


@pytest.mark.parametrize(
    ("article_no", "deadline", "event"),
    [
        (141131, kst(2026, 11, 10, 23, 59), None),
        (141483, None, kst(2026, 11, 6, 9, 30)),
        # 학생성공센터: the event date is written with a two-digit year (`26. 11. 11.(수)`).
        (141051, kst(2026, 11, 5, 23, 59), kst(2026, 11, 11, 13, 30)),
        # 재난안전 해커톤: `참가방법 : 이메일 접수(~11.12.(목) 23시 59분)`.
        (141478, kst(2026, 11, 12, 23, 59), None),
        # 자기이해: four sessions under one heading, so no event date (it used to be 11/2 15:00).
        (140500, None, None),
    ],
)
def test_real_notices_end_to_end(article_no, deadline, event):
    got = extract_from_body(body(article_no), POSTED)
    assert (got.deadline_at, got.event_at) == (deadline, event)


def test_four_sessions_are_flagged_so_the_title_cannot_stand_in():
    got = extract_from_body(body(140500), POSTED)
    assert (got.event_at, got.event_ambiguous) == (None, True)


def test_rich_text_body_is_flattened_by_block():
    html = (
        '<dl class="board-write-box"><dt>내용</dt>'
        "<dd><p>모집기간 : 11/9까지<br>문의 : 02-123-4567</p><div>일시: 11/20</div></dd></dl>"
    )
    assert parse_detail(html).splitlines() == [
        "모집기간 : 11/9까지",
        "문의 : 02-123-4567",
        "일시: 11/20",
    ]


def test_post_without_text_is_empty_not_an_error():
    assert parse_detail('<dl class="board-write-box"><dd><pre class="pre"> </pre></dd></dl>') == ""


@pytest.mark.parametrize("html", ["", "<html><body>삭제된 게시글입니다.</body></html>"])
def test_page_without_the_body_box_raises(html):
    with pytest.raises(ParseError):
        parse_detail(html)
