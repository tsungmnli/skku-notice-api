from datetime import date
from pathlib import Path

from app.crawler.fetch import FetchError
from scripts.audit_deadlines import (
    Notice,
    NoticeAudit,
    audit_notice,
    clue_candidate,
    main,
    render,
    run_audit,
    summarize,
    visible,
)

FIXTURES = Path(__file__).parent / "fixtures"
POSTED = date(2026, 10, 30)


def page(article_no: int) -> str:
    return (FIXTURES / f"detail_{article_no}.html").read_text(encoding="utf-8")


class Pages:
    def __init__(self, bodies: dict[int, str], fail: set[int] = frozenset()):
        self.bodies, self.fail = bodies, fail

    def detail_page(self, article_no: int) -> str:
        if article_no in self.fail:
            raise FetchError(f"{article_no}: server down")
        body = self.bodies[article_no]
        return f'<dl class="board-write-box"><dd><pre>{body}</pre></dd></dl>'


def test_candidate_is_the_word_in_front_of_the_date():
    assert clue_candidate("○ 참가방법 : 이메일 접수(~") == "접수"
    assert clue_candidate("서류제출 : ") == "서류제출"
    assert clue_candidate("- ") is None


def test_a_read_line_is_marked_and_a_missed_one_names_its_candidate():
    body = "모집기간 : 11/1~11/5\n서류제출일 : 11/9 17:00 까지\n문의 : 02-123-4567"
    audit = audit_notice(Notice(1, "채용", POSTED), body)
    assert audit.body.deadline_at is not None
    assert [(line, read, word) for line, read, word in audit.lines] == [
        ("모집기간 : 11/1~11/5", True, None),
        ("서류제출일 : 11/9 17:00 까지", False, "서류제출일"),
    ]


def test_clue_on_the_line_before_still_counts_as_read():
    # 학생성공센터: `일시 및 장소` and its date are on two lines.
    audit = audit_notice(Notice(141051, "공모전 진행자 모집", POSTED), _body(141051))
    event_line = [read for line, read, _ in audit.lines if "26. 11. 11." in line]
    assert event_line == [True]


def _body(article_no: int) -> str:
    from app.crawler.parser import parse_detail

    return parse_detail(page(article_no))


def test_known_clue_words_are_never_suggested():
    audit = audit_notice(Notice(1, "t", POSTED), "접수기간 : 2019.1.1\n일시 : 4월 31일\n마감 11/9")
    assert all(word is None for _, _, word in audit.lines)


def test_a_title_deadline_counts_even_when_the_body_has_none():
    audit = audit_notice(Notice(1, "채용 (~11.9까지)", POSTED), "많은 지원 바랍니다.")
    assert audit.has_deadline and audit.body.deadline_at is None


def test_run_audit_keeps_going_after_a_failed_page():
    source = Pages({1: "마감 : 11/9", 2: "x"}, fail={2})
    audits = run_audit([Notice(1, "a", POSTED), Notice(2, "b", POSTED)], source)
    assert [a.error is None for a in audits] == [True, False]
    assert "server down" in render(audits[1])
    assert "읽은 글 1건 (실패 1건)" in summarize(audits)


def test_summary_counts_misses_and_ranks_candidate_words():
    source = Pages(
        {1: "서류제출일 : 11/9", 2: "서류제출일 : 11/10\n등록처 : 11/11", 3: "마감 : 11/9"}
    )
    notices = [Notice(n, f"t{n}", POSTED) for n in (1, 2, 3)]
    text = summarize(run_audit(notices, source))
    assert "마감 있음 1건, 마감 없음 2건" in text
    assert text.index("서류제출일  x2") < text.index("등록처  x1")


def test_main_reads_saved_pages_without_network_or_database(capsys):
    args = [str(FIXTURES / "detail_141478.html"), "--posted", "2026-10-30"]
    assert main(["--html", *args[:1], *args[1:]]) == 0
    out = capsys.readouterr().out
    assert "본문 마감 2026-11-12 23:59" in out
    assert "마감 있음 1건, 마감 없음 0건" in out


# --- Notices whose deadline or event date was emptied on purpose ---------------------------

TWO_DEADLINES = "접수마감 : 11/9\n제출마감 : 11/12"
EVENT_RANGE = "일시 : 11/16(월) ~ 11/17(화)"


def test_several_deadlines_are_flagged_and_shown():
    audit = audit_notice(Notice(7, "채용", POSTED), TWO_DEADLINES)
    assert audit.body.deadline_at is None and audit.deadline_ambiguous
    assert not audit.event_ambiguous and audit.ambiguous
    assert "⚠ 마감 모호" in render(audit)
    assert "⚠ 행사일 모호" not in render(audit)


def test_a_range_or_sessions_are_flagged_as_an_ambiguous_event():
    for body in (EVENT_RANGE, "일시\n① 11/5 10:00\n② 11/12 10:00"):
        audit = audit_notice(Notice(7, "특강", POSTED), body)
        assert audit.event_at is None and audit.event_ambiguous, body
        assert "⚠ 행사일 모호" in render(audit)
        assert "⚠ 마감 모호" not in render(audit)


def test_the_title_does_not_stand_in_for_an_ambiguous_event():
    audit = audit_notice(Notice(7, "[특강] 11/20(금) 특강", POSTED), EVENT_RANGE)
    assert audit.event_at is None and audit.event_ambiguous


def test_a_title_deadline_wins_over_an_ambiguous_body():
    # Such a notice is never read from its body, so nothing was emptied.
    audit = audit_notice(Notice(7, "채용 (~11.9까지)", POSTED), TWO_DEADLINES)
    assert audit.body.deadline_ambiguous and not audit.deadline_ambiguous
    assert not audit.ambiguous and "⚠" not in render(audit)


def test_a_clear_notice_has_no_flag():
    audit = audit_notice(Notice(7, "행사", POSTED), "마감 : 11/9\n일시 : 11/20 14:00")
    assert audit.body.deadline_at is not None and audit.event_at is not None
    assert not audit.ambiguous and "⚠" not in render(audit)


def test_the_summary_counts_and_names_the_flagged_notices():
    bodies = {
        1: TWO_DEADLINES,
        2: EVENT_RANGE,
        3: f"{TWO_DEADLINES}\n{EVENT_RANGE}",
        4: "마감 : 11/9\n일시 : 11/20 14:00",
    }
    audits = run_audit([Notice(n, f"t{n}", POSTED) for n in bodies], Pages(bodies))
    lines = summarize(audits).splitlines()
    assert "모호해서 비운 글: 마감 2건, 행사일 2건" in lines
    assert "  마감: #1 #3" in lines
    assert "  행사일: #2 #3" in lines


def test_the_summary_without_flags_says_zero_and_lists_nothing():
    audits = run_audit([Notice(4, "t4", POSTED)], Pages({4: "마감 : 11/9"}))
    text = summarize(audits)
    assert "모호해서 비운 글: 마감 0건, 행사일 0건" in text
    assert "  마감:" not in text and "  행사일:" not in text


def test_a_failed_page_is_not_counted_as_flagged():
    audits = run_audit([Notice(2, "b", POSTED)], Pages({2: "x"}, fail={2}))
    assert "모호해서 비운 글: 마감 0건, 행사일 0건" in summarize(audits)


def test_ambiguous_only_keeps_the_flagged_and_the_failed():
    clean = audit_notice(Notice(1, "a", POSTED), "마감 : 11/9")
    flagged = audit_notice(Notice(2, "b", POSTED), EVENT_RANGE)
    failed = NoticeAudit(Notice(3, "c", POSTED), error="server down")
    audits = [clean, flagged, failed]
    assert visible(audits, ambiguous_only=False) == audits
    assert visible(audits, ambiguous_only=True) == [flagged, failed]


def test_main_flags_the_real_self_understanding_notice(capsys):
    # 140500: four sessions under `1. 일시 (4회 중 택 1)`, so no single event date.
    path = str(FIXTURES / "detail_140500.html")
    assert main(["--html", path, "--posted", "2026-10-26"]) == 0
    out = capsys.readouterr().out
    assert "⚠ 행사일 모호" in out and "⚠ 마감 모호" not in out
    assert "행사일 -" in out
    assert "모호해서 비운 글: 마감 0건, 행사일 1건" in out
    assert "  행사일: detail_140500.html" in out  # a saved page has no number: its name


def test_main_ambiguous_only_hides_clear_notices_but_still_counts_them(capsys):
    clear, flagged = (str(FIXTURES / f"detail_{no}.html") for no in (141478, 140500))
    assert main(["--html", clear, flagged, "--ambiguous-only", "--posted", "2026-10-26"]) == 0
    out = capsys.readouterr().out
    assert "detail_140500.html" in out.split("읽은 글")[0]
    assert "detail_141478.html" not in out
    assert "읽은 글 2건" in out
