from pathlib import Path

import pytest

from app.crawler.fingerprint import changed_lines, fingerprint, normalize_lines
from app.crawler.parser import ParseError

FIXTURES = Path(__file__).parent / "fixtures"
PAGE = (FIXTURES / "detail_141051.html").read_text(encoding="utf-8")


def test_the_same_page_has_the_same_fingerprint():
    assert fingerprint(PAGE) == fingerprint(PAGE)


def test_reflowing_the_page_is_not_an_edit():
    # Blank lines, trailing spaces, tabs and no-break spaces are layout.
    reflowed = PAGE.replace("\n", " \n\n").replace("제출 기한", "제출\xa0기한")
    assert fingerprint(reflowed).text_hash == fingerprint(PAGE).text_hash
    assert fingerprint(reflowed).box_hash == fingerprint(PAGE).box_hash


def test_a_changed_word_in_the_body_changes_both_hashes():
    edited = PAGE.replace("e-mail로 제출", "방문 제출")
    old, new = fingerprint(PAGE), fingerprint(edited)
    assert old.text_hash != new.text_hash
    assert old.box_hash != new.box_hash


def test_noise_next_to_the_body_moves_only_the_box_hash():
    noisy = PAGE.replace("<dd>", "<dd><span>조회수 12</span>", 1)
    old, new = fingerprint(PAGE), fingerprint(noisy)
    assert old.text_hash == new.text_hash
    assert old.box_hash != new.box_hash


def test_scripts_and_styles_in_the_box_are_ignored():
    scripted = PAGE.replace("<dd>", "<dd><script>var n = 1;</script><style>a{}</style>", 1)
    assert fingerprint(scripted) == fingerprint(PAGE)


def test_a_page_without_the_write_box_is_a_parse_error():
    with pytest.raises(ParseError):
        fingerprint("<html>삭제된 게시글입니다.</html>")


def test_normalize_lines_drops_blank_lines_and_collapses_spaces():
    assert normalize_lines("  a   b \n\n\t\nc​\n") == ("a b", "c")


def test_changed_lines_shows_what_went_and_what_came():
    old = ("same", "old deadline 11/5", "gone")
    new = ("same", "new deadline 11/12")
    assert changed_lines(old, new) == ["- old deadline 11/5", "- gone", "+ new deadline 11/12"]


def test_changed_lines_caps_the_output():
    old = tuple(f"a{i}" for i in range(10))
    out = changed_lines(old, (), limit=3)
    assert len(out) == 4 and out[-1] == "… and 7 more changed lines"
