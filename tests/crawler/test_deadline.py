from datetime import date, datetime

import pytest

from app.crawler.deadline import extract_deadline
from app.timeutil import KST

POSTED = date(2026, 10, 23)


def kst(*args):
    return datetime(*args, tzinfo=KST)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Real titles from the 2026-10-24 list page.
        ("성균타임즈 2026 ESSAY CONTEST 개최 (~10/31)", kst(2026, 10, 31, 23, 59)),
        ("자연과학캠퍼스 통학 셔틀버스 시범운행 안내 (10/19~12/18)", kst(2026, 12, 18, 23, 59)),
        ("교육생 모집 (신청 기간 : 11.02~11.14)", kst(2026, 11, 14, 23, 59)),
        ("행정 직원 모집 (~10/26 월 오전 11시)", kst(2026, 10, 26, 11, 0)),
        # Not deadlines.
        ("수선포럼 개최 안내: 2026.11.18.(수) 11:30~13:00", None),
        ("2026학년도 2학기 장학생 선발 안내", None),
        ("[IDEC] 제14기 교육생 모집(1명)", None),
        ("(자)1398피트니스센터 11월 등록 안내", None),
        # Other spellings.
        ("접수 마감: 2026.11.05.", kst(2026, 11, 5, 23, 59)),
        ("마감일 2026-11-05 17:00", kst(2026, 11, 5, 17, 0)),
        ("11월 5일(수) 17:00까지 제출", kst(2026, 11, 5, 17, 0)),
        ("11/5(수)까지 신청", kst(2026, 11, 5, 23, 59)),
        ("~11.5 오후 6시", kst(2026, 11, 5, 18, 0)),
        ("~11.5 오후 12시", kst(2026, 11, 5, 12, 0)),
        ("~11.5 오전 12시", kst(2026, 11, 5, 0, 0)),
        ("신청: 2026년 11월 1일 ~ 2026년 11월 5일 18:30", kst(2026, 11, 5, 18, 30)),
        # Several marked dates: the final one wins.
        ("1차 ~10/30, 2차 ~11/6", kst(2026, 11, 6, 23, 59)),
        # Year rollover, and impossible dates ignored.
        ("12/20~1/10 모집", kst(2027, 1, 10, 23, 59)),
        ("~2/30", None),
        ("~13/1", None),
    ],
)
def test_extract(text, expected):
    posted = date(2026, 12, 20) if "12/20~1/10" in text else POSTED
    assert extract_deadline(text, posted) == expected


def test_year_without_marker_rolls_forward_for_early_months():
    # Posted in late December, "~1/15" must mean next January.
    assert extract_deadline("(~1/15)", date(2026, 12, 28)) == kst(2027, 1, 15, 23, 59)


def test_recent_past_deadline_stays_in_this_year():
    # Posted 10/23, a stale "~10/20" is a real (already passed) date, not next year's.
    assert extract_deadline("(~10/20)", POSTED) == kst(2026, 10, 20, 23, 59)
