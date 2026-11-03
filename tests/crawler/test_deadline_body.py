from datetime import date, datetime

import pytest

from app.crawler.deadline import (
    event_for,
    extract_deadline,
    extract_event_from_title,
    extract_from_body,
)
from app.timeutil import KST

POSTED = date(2026, 11, 2)


def kst(*args):
    return datetime(*args, tzinfo=KST)


# Real bodies of the two notices that showed no D-day.
QUANTUM = """\
성균관대학교 양자생명물리과학원에서 근무할 직원을 아래와 같이 모집합니다.

1. 모집부문 및 근무조건
 가. 채용분야 및 인원 : 행정 1명
 다. 근무조건
    (2) 주5일 근무(주중 09:00~17:30)
    (4) 근무장소 : 성균관대학교 자연과학캠퍼스 N센터 (경기도 수원)

3. 제출서류
 - 첨부의 이력서 서식으로 파일명 기재 후, 이메일 제출(ohjuyi@skku.edu)

4. 모집기간 및 지원방법
 가. 모집기간 : 2026.10.30(금)~2026.11.10(화)까지
 나. 지원방법 : 이메일지원 ohjuyi@skku.edu

5. 전형절차
 가. 1단계 : 서류전형
 나. 2단계 : 면접 (서류전형 합격자에 한해 면접일자 개별통보)

6. 문의 및 접수: 양자생명물리과학원
                      ohjuyi@skku.edu, 031-299-4790
"""

COLLOQUIUM = """\
11/6(금) 인공지능학과에서 주관하는 SKKU AI Colloquium 2026 행사 지원 아르바이트 모집

ㅇ 모집인원 : 3명
ㅇ 일시 : 11/06(금) 09:30~18:30
ㅇ 업무 : 행사 진행 도움 (참석자 확인 및 등록 등등)
ㅇ 급여 : 11,000원/시간
ㅇ근무장소 : 자연과학캠퍼스 대강당 (의학관 옆)
ㅇ 지원요건 : 학부생 (재학생만 가능/휴학생 불가)

희망 지원자는 아래의 링크를 통해 지원해주시기 바랍니다.
https://forms.gle/aRnsbvaTqcBSoSrQA

ㅇ 문의 : 인공지능학과 031-290-5828/5929 , aigs@skku.edu
"""


def test_deadline_in_a_recruitment_body():
    got = extract_from_body(QUANTUM, POSTED)
    assert got.deadline_at == kst(2026, 11, 10, 23, 59)
    assert got.event_at is None
    assert got.deadline_evidence == "가. 모집기간 : 2026.10.30(금)~2026.11.10(화)까지"


def test_event_date_is_not_a_deadline():
    got = extract_from_body(COLLOQUIUM, POSTED)
    assert got.deadline_at is None
    assert got.deadline_evidence is None
    assert got.event_at == kst(2026, 11, 6, 9, 30)


@pytest.mark.parametrize(
    ("body", "deadline", "event"),
    [
        # Clue and date on the same line, in either order.
        (
            "접수기간: 11/1~11/5 / 일시: 11/20 14:00",
            kst(2026, 11, 5, 23, 59),
            kst(2026, 11, 20, 14, 0),
        ),
        ("일시: 11/20 14:00 / 마감: 11/3 17:00", kst(2026, 11, 3, 17, 0), kst(2026, 11, 20, 14, 0)),
        # The date is on the line after the clue.
        ("신청 기간\n - 2026.11.4 ~ 11.9 18:00", kst(2026, 11, 9, 18, 0), None),
        # But not two lines later.
        ("신청기간\n 아래를 참고하세요\n 11/9", None, None),
        # `모집일정` is a deadline clue even though it contains the event clue `일정`.
        ("모집일정 : 11/3~11/12", kst(2026, 11, 12, 23, 59), None),
        # Two different deadlines in one line: which one holds is not ours to guess.
        ("접수기간 연장 : 11/15까지 (기존 11/10)", None, None),
        # Words that merely contain a clue.
        ("일시적으로 11/9 운영", None, None),
        # Dates with no clue are ignored; so are phone numbers and implausible dates.
        ("문의 031-290-5828 / 11/9까지 회신", None, None),
        ("마감 : 2019.1.1", None, None),
        ("마감 : 2.30", None, None),
        # Spacing inside a clue word does not matter.
        ("접 수 기 간 : 11/8", kst(2026, 11, 8, 23, 59), None),
    ],
)
def test_body_cases(body, deadline, event):
    got = extract_from_body(body, POSTED)
    assert (got.deadline_at, got.event_at) == (deadline, event)


def test_range_rolls_into_next_year():
    got = extract_from_body("모집기간 : 12/20~1/10", date(2026, 12, 20))
    assert got.deadline_at == kst(2027, 1, 10, 23, 59)


def test_evidence_is_capped():
    got = extract_from_body("마감 : 11/9 " + "가" * 300, POSTED)
    assert got.deadline_evidence is not None and len(got.deadline_evidence) == 100


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "SKKU AI Colloquium 2026 행사 지원 아르바이트 모집 안내 (11/6 금)",
            kst(2026, 11, 6, 23, 59),
        ),
        ("[카운슬링센터] 11/20(금) 오후 1시 불안관리 워크숍 참여자 모집", kst(2026, 11, 20, 13, 0)),
        ("수선포럼 개최 안내: 2026.11.18.(수) 11:30~13:00", kst(2026, 11, 18, 11, 30)),
        ("특강 안내 11월 6일", kst(2026, 11, 6, 23, 59)),
        # A deadline in the title: nothing left for an event.
        ("행정조교 채용(~11.09(월) 17:00까지)", None),
        # Not dates.
        ("Python 3.12 특강 안내", None),
        ("2026-2 성균인의 자기이해 참가자 모집", None),
        ("배율 1.5 적용 안내", None),
        # A weekday-looking word after the date is not a weekday.
        ("11/6 일정 안내", None),
    ],
)
def test_event_from_title(title, expected):
    assert extract_event_from_title(title, POSTED) == expected


# ---------------------------------------------------------------------------------------------
# Forms found by the first audit: `~11.12.(목) 23시 59분`, a two-digit year, a method clue.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "deadline", "event"),
    [
        # 재난안전 해커톤: the deadline sits in brackets after the way to apply.
        ("○ 참가방법 : 이메일 접수(~11.12.(목) 23시 59분)", kst(2026, 11, 12, 23, 59), None),
        ("제출방법 : 이메일 (11/9 17시까지)", kst(2026, 11, 9, 17, 0), None),
        ("접수방법 : 11/9 오후 5시", kst(2026, 11, 9, 17, 0), None),
        # 학생성공센터: `26. 11. 11.(수)` is year.month.day, not month 26.
        ("일시 및 장소\n - 26. 11. 11.(수) 13:30~17:00/ 경영관", None, kst(2026, 11, 11, 13, 30)),
        ("마감 : 26.11.30", kst(2026, 11, 30, 23, 59), None),
        # Without a year the month.day reading stays (`11.12.` is not year 11).
        ("마감 : 11.12.(목)", kst(2026, 11, 12, 23, 59), None),
        # `3시간` is a duration, not 3 o'clock.
        ("마감 : 11/9 3시간 이내", kst(2026, 11, 9, 23, 59), None),
        # A way to apply without a date says nothing.
        ("지원방법 : 이메일지원 ohjuyi@skku.edu", None, None),
    ],
)
def test_forms_from_the_first_audit(body, deadline, event):
    got = extract_from_body(body, POSTED)
    assert (got.deadline_at, got.event_at) == (deadline, event)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("해커톤 신청(~11.12.(목) 23시 59분)", kst(2026, 11, 12, 23, 59)),
        ("모집 ~11.12.(목) 17시까지", kst(2026, 11, 12, 17, 0)),
        ("공모 ~26.11.12까지", kst(2026, 11, 12, 23, 59)),
        # Not a deadline: a duration, a version, a phone number.
        ("3시간 특강 안내", None),
        ("v1.2.3 배포 안내", None),
    ],
)
def test_deadline_in_title_forms(title, expected):
    assert extract_deadline(title, POSTED) == expected


# ---------------------------------------------------------------------------------------------
# Step 7-1a: forms found by the second audit and the saved pages.
# ---------------------------------------------------------------------------------------------

# Real body of notice 141425. The deadline was read as an event date: `접수(응모)기간` was no clue
# word, so the date fell to the `■ 일정` heading above it.
DORM_CONTEST = """\
기숙사 생활의 다양한 문화와 더욱 즐거운 기숙사 생활을 위한 참신한 아이디어를 공유하는
기숙사 생활콘텐츠 공모전을 실시합니다.

■ 참여대상
 ① 기숙사에 거주했었거나 현재 거주하고 있는 개인 또는 2인 1팀으로 참가 가능

■ 응모 부문 (택1)
 ① 영상
 - mp4 형식, 용량 200MB 이내
 ② 웹툰
 - (가로) 640pixel 이상, (해상도) 300dpi 이상

■ 일정
- 접수(응모)기간: 2026. 10. 19.(월) ~ 2026. 11. 16.(월)
- 결과발표: 2026. 11. 24.(화)  (홈페이지 공지 및 개별 연락)

  * 일정은 접수 현황에 따라 연장되거나 변동될 수 있음
  * 접수 기간 종료 후에는 신청서 접수, 콘텐츠 업로드 및 수정 불가

☎ 문의 02-760-0168 / 031-290-5185
"""


def test_two_names_in_brackets_is_a_deadline_clue():
    got = extract_from_body(DORM_CONTEST, POSTED)
    assert got.deadline_at == kst(2026, 11, 16, 23, 59)
    assert got.event_at is None  # the 11/24 result day has no clue word and is left alone
    assert got.deadline_evidence == "- 접수(응모)기간: 2026. 10. 19.(월) ~ 2026. 11. 16.(월)"


@pytest.mark.parametrize(
    ("body", "deadline", "event"),
    [
        # 학점교류: `(마감)` says the line is over; it is not a clue and its date is no deadline.
        # (Posted 10/20 so the date is not thrown out for being in the past; only the clue matters.)
        ("(마감)충남대학교: 2026. 10. 30.(금) 13:00까지", None, None),
        ("- [마감] 서울대학교 마감 : 11/3", kst(2026, 11, 3, 23, 59), None),
        # DS 교육센터 TA: the word comes after the date; it must not lend its clue to the next line.
        ("2026. 10. 28(수) ~ 11. 15(일) 자정 마감", kst(2026, 11, 15, 23, 59), None),
        (
            "2026. 10. 28(수) ~ 11. 15(일) 자정 마감\n- 2026. 11. 20.(금) 14:00",
            kst(2026, 11, 15, 23, 59),
            None,
        ),
        ("11/9(월) 17시 마감", kst(2026, 11, 9, 17, 0), None),
        # `마감일`, `마감되었습니다` are not `date + 마감`.
        ("11/9 마감되었습니다", None, None),
        ("11/9 마감일은 추후 공지", None, None),
        # 정오 and 자정 (141142 read `정오(12:00)까지` as 23:59).
        ("마감 : 11/9 정오(12:00)까지", kst(2026, 11, 9, 12, 0), None),
        ("신청기간 : 11/2 ~ 11/9 정오까지", kst(2026, 11, 9, 12, 0), None),
        ("신청기간 : 11/2 ~ 11/9 자정까지", kst(2026, 11, 9, 23, 59), None),
    ],
)
def test_forms_from_the_second_audit(body, deadline, event):
    posted = date(2026, 10, 20) if body.startswith("(마감)") else POSTED
    got = extract_from_body(body, posted)
    assert (got.deadline_at, got.event_at) == (deadline, event)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("모집 ~11.12.(목) 정오까지", kst(2026, 11, 12, 12, 0)),
        ("모집 ~11.12.(목) 자정까지", kst(2026, 11, 12, 23, 59)),
    ],
)
def test_noon_and_midnight_in_title(title, expected):
    assert extract_deadline(title, POSTED) == expected


# ---------------------------------------------------------------------------------------------
# Step 7-1b: two different deadlines give none.
# ---------------------------------------------------------------------------------------------

# Real body of notice 140495 (the long rules below the list are left out). Each school has its
# own date, the board marks a school `(마감)` by hand and always late, so no single date is right.
CREDIT_EXCHANGE = """\
2026학년도 겨울 계절수업 타 대학 학점교류를 희망하는 학생은 아래 안내에 따라 신청하시기 바랍니다.
(게시일 기준으로 교류대학 누적 업데이트 됩니다.)


가. 추천 마감일 (본교에서 타대학 안내공문을 수령 시 누적 업데이트 예정)

※ GLS를 통해 신청하는 학교별 접수기한은 각 학교에서 안내하는 공문접수마감보다 이릅니다.
   아래 학교별 마감기한을 준수하여 GLS로 신청하시기 바랍니다.


- (마감)서울대학교:  2026. 10. 16.(금) 13:00까지
  [교류사항 일반, 혁신공유 중 선택하여 서식 작성 및 제출, 중복 불가]
- (마감)한국예술종합학교:  2026. 10. 27.(화) 13:00까지
- (마감)충남대학교:  2026. 10. 30.(금) 13:00까지
- 부산대학교:  2026. 11. 2.(월) 13:00까지
- 제주대학교:  2026. 11. 4.(수) 13:00까지
- 고려대학교:  2026. 11. 5.(목) 13:00까지
- 울산대학교:  2026. 11. 12.(목) 13:00까지

※ 추천마감 기한 이후 신청은 절대 불가 하며, 시스템상 신청이 가능하더라도 탈락 처리

나. 신청자격
- 학사과정 학생으로 2개 학기 이상을 이수 완료한 자
"""


@pytest.mark.parametrize("posted", [date(2026, 10, 12), date(2026, 10, 20), POSTED])
def test_a_list_of_per_school_deadlines_gives_none(posted):
    # Whatever the posting date: with 10/12 the first (closed) school used to be read alone.
    got = extract_from_body(CREDIT_EXCHANGE, posted)
    assert got.deadline_at is None
    assert got.deadline_evidence is None
    assert got.deadline_ambiguous is True


@pytest.mark.parametrize(
    ("body", "deadline", "ambiguous"),
    [
        # The same day named twice is one deadline.
        ("접수기간 : 11/1~11/5\n마감 : 11/5", kst(2026, 11, 5, 23, 59), False),
        # ... and the time written on one of them is kept.
        ("접수기간 : 11/1~11/5\n마감 : 11/5 18:00", kst(2026, 11, 5, 18, 0), False),
        ("마감 : 11/5 18:00\n접수기간 : 11/1~11/5", kst(2026, 11, 5, 18, 0), False),
        # Two times on one day disagree.
        ("마감 : 11/5 12:00 / 마감 : 11/5 18:00", None, True),
        # Two days disagree, in one line or in two.
        ("모집기간 : 11/1~11/5\n마감 : 11/9", None, True),
        ("접수기간 : 11/1~11/5 / 서류마감 : 11/9", None, True),
        ("마감 : 11/9 / 문의 11/20", None, True),
        # A range is one deadline, its end; so is a range that spells out the year or the time.
        (
            "접수기간 : 2026. 10. 28.(수) 09:00 ~ 2026. 11. 9.(월) 18:00",
            kst(2026, 11, 9, 18, 0),
            False,
        ),
        ("신청기간 : 11/2 ~ 11/9", kst(2026, 11, 9, 23, 59), False),
        # A deadline before the posting date is over; there is no grace any more.
        ("마감 : 11/1", None, False),
        ("마감 : 11/2", kst(2026, 11, 2, 23, 59), False),
        # `날짜까지` twice with different dates: ambiguous even though only one has a clue word.
        ("신청기간 : 11/9까지\n서류 제출은 11/12까지", None, True),
        ("신청기간 : 11/9까지\n11/12까지 서류 제출", None, True),
        # The same date twice, or a range's end, is not.
        ("모집기간 : 11/1~11/9까지\n서류는 11/9까지 제출", kst(2026, 11, 9, 23, 59), False),
        # A `까지` date far from the posting date (a year, a phone number) is not a rival.
        ("신청기간 : 11/9까지\n2019. 3. 5.까지 운영했던 제도", kst(2026, 11, 9, 23, 59), False),
    ],
)
def test_two_different_deadlines_give_none(body, deadline, ambiguous):
    got = extract_from_body(body, POSTED)
    assert got.deadline_at == deadline
    assert got.deadline_ambiguous is ambiguous


# ---------------------------------------------------------------------------------------------
# Step 7-1c: an event that is a range or has several sessions gives no event date.
# ---------------------------------------------------------------------------------------------

# Real body of notice 140500 (saved as a fixture too). The heading `1. 일시 (4회 중 택 1)` has no
# date; four sessions follow. It was read as `11/2 15:00`, the first session only. The later
# `※ 위 일정 중` and the `①②③④` of section 4 must not start a second event group.
SELF_UNDERSTANDING = """\
성균관대학교의 전통과 가치를 이해하고,
수기치인(修己治人)과 인의예지(仁義禮智)를 실천하고자 하는
성균인을 위한 특별프로그램에 여러분을 초대합니다.

1. 일시 (4회 중 택 1)
① 2026. 11. 02.(월) 15:00~17:00
② 2026. 11. 09.(월) 15:00~17:00
③ 2026. 11. 16.(월) 15:00~17:00
④ 2026. 11. 23.(월) 15:00~17:00
※ 위 일정 중 원하는 날짜 1회를 선택하여 신청해 주세요.

2. 장소
국제관 9B215호(집결 장소) 및 성균박물관, 명륜당 등

3. 대상
성균관대학교 재학생 누구나

4. 프로그램 내용
① 명륜당 및 성균관 역사 탐방
② 성균박물관 탐방
③ 유생복 착용 및 예절 체험
④ 『논어』·『중용』 성독 및 성찰
※ 우천 시에도 실내 특별프로그램으로 정상 운영합니다.

5. 참여 학생 인센티브
① 창의품 2시간 부여
② 2026년 신입생 FYE 세미나 15점 부여
(학부대학 대계열 입학생에 한함)

6. 참여 방법
** 챌린지스퀘어 > 비교과 프로그램 > 인성함양 실천 특별프로그램 [성균] **에서 신청
※ 4회 중 1회를 선택하여 신청해 주세요.

7. 문의
성균인성교육센터
전화: 02-760-0165
이메일: chec@skku.edu
성균관대학교의 역사와 전통을 직접 체험하고,
성균인으로서의 자긍심과 인성을 함양하는 뜻깊은 시간에 많은 관심과 참여 바랍니다.
"""


def test_sessions_under_a_heading_give_no_event_date():
    got = extract_from_body(SELF_UNDERSTANDING, POSTED)
    assert got.event_at is None
    assert got.event_ambiguous is True


@pytest.mark.parametrize(
    ("body", "event", "ambiguous"),
    [
        # A range is no single day: it is left empty until the schema can hold both ends.
        ("일시: 11/16(월) ~ 11/17(화)", None, True),
        ("일시: 11/16(월) 14:00 ~ 11/17(화) 17:00", None, True),
        ("일시 : 2026. 11. 16.(월) ~ 2026. 11. 20.(금)", None, True),
        # Two ends on one day, or two times on one date, are one day.
        ("일시: 11/16(월) 14:00 ~ 11/16(월) 17:00", kst(2026, 11, 16, 14, 0), False),
        ("일시: 11/16(월) 14:00 ~ 17:00", kst(2026, 11, 16, 14, 0), False),
        # Two different days on one line.
        ("일시: 11/5 / 11/12", None, True),
        ("일시: 11/5(수), 11/6(목), 11/7(금) 각 10:00", None, True),
        # Sessions on separate lines: the same day at two times keeps the day, not a time.
        ("일시\n① 11/5 10:00\n② 11/5 13:00", kst(2026, 11, 5, 23, 59), False),
        # A time on only one of them is kept.
        ("일시\n① 11/5\n② 11/5 13:00", kst(2026, 11, 5, 13, 0), False),
        # Lines without a date may sit between two sessions (up to MAX_EVENT_GAP of them).
        (
            "일시 (2회)\n① 11/5 10:00\n※ 장소는 추후 안내\n※ 문의는 아래로\n② 11/6 10:00",
            None,
            True,
        ),
        # Three lines in between end the group: only what came before is read (a known limit).
        (
            "일시 (2회)\n① 11/5 10:00\n※ 가\n※ 나\n※ 다\n② 11/6 10:00",
            kst(2026, 11, 5, 10, 0),
            False,
        ),
        # A `날짜까지` line below the event is a deadline, not one more session.
        ("일시\n11/20(금) 14:00\n신청 : 11/15(일)까지", kst(2026, 11, 20, 14, 0), False),
        # A clue word ends the group; what follows it is not read as a session.
        (
            "일시\n① 11/2 15:00\n접수기간 : 11/1 ~ 11/5\n② 11/9 15:00",
            kst(2026, 11, 2, 15, 0),
            False,
        ),
        # Dates on the clue's own line are the whole group; later lines are not collected.
        ("일시: 11/5 14:00\n장소: 학관\n문의: 11/20", kst(2026, 11, 5, 14, 0), False),
        # The first clue that has dates wins; a heading with nothing under it does not count.
        ("일시: 11/5 14:00\n일시: 11/12", kst(2026, 11, 5, 14, 0), False),
        ("일정: 추후 공지\n일시: 11/12 10:00", kst(2026, 11, 12, 10, 0), False),
        # After the first group a second event clue must not start reading the next line.
        ("일시: 11/5 14:00\n일정은 변동될 수 있음\n11/20", kst(2026, 11, 5, 14, 0), False),
    ],
)
def test_event_groups(body, event, ambiguous):
    got = extract_from_body(body, POSTED)
    assert (got.event_at, got.event_ambiguous) == (event, ambiguous)


def test_a_group_does_not_touch_the_deadline():
    body = "일시\n① 11/12 15:00\n② 11/19 15:00\n접수기간 : 11/1 ~ 11/10"
    got = extract_from_body(body, POSTED)
    assert got.event_ambiguous is True
    assert got.deadline_at == kst(2026, 11, 10, 23, 59)


@pytest.mark.parametrize("posted", [date(2026, 10, 12), date(2026, 10, 20), POSTED])
def test_saved_bodies_keep_their_event_date(posted):
    # 141425 and 140495 have no event clue with a date; nothing may be invented for them.
    for text in (DORM_CONTEST, CREDIT_EXCHANGE):
        got = extract_from_body(text, posted)
        assert (got.event_at, got.event_ambiguous) == (None, False)


def test_title_stands_in_only_when_the_body_has_no_event_clue():
    title = "자기이해 프로그램 참가자 모집 (11/2 월)"
    # Several sessions: the title's one date must not come back.
    assert event_for(extract_from_body(SELF_UNDERSTANDING, POSTED), title, POSTED) is None
    # Nothing about an event in the body: the title's date is used.
    no_event = extract_from_body("모집기간 : 11/9", POSTED)
    assert event_for(no_event, title, POSTED) == kst(2026, 11, 2, 23, 59)
    # The body's date, with its time, beats the title's.
    got = extract_from_body("일시 : 11/2 15:00", POSTED)
    assert event_for(got, title, POSTED) == kst(2026, 11, 2, 15, 0)


@pytest.mark.parametrize(
    "line",
    [
        "2. 잔여 APC 추가 지원 기간 및 활용 방법: 2026. 11. 3.(화) ~ 소진 시까지",
        "지원 기간 : 2026. 11. 3.(화) ~ 예산 소진 시까지",
        "접수기간 : 11/3 ~ 별도 공지 시까지",
    ],
)
def test_a_start_date_with_no_end_is_no_deadline(line):
    # `11/3 ~ 소진 시까지` opens a period; the date is its start, not a deadline.
    assert extract_from_body(line, date(2026, 10, 22)).deadline_at is None


def test_a_range_with_an_end_date_still_gives_the_end():
    body = "접수기간 : 11/3 ~ 11/10까지"
    assert extract_from_body(body, date(2026, 10, 22)).deadline_at == kst(2026, 11, 10, 23, 59)


@pytest.mark.parametrize(
    "line, event",
    [
        # Real notice 141534: a comma between the weekday and the time used to drop the time.
        (
            "4. 일시 및 장소: 2026년 11월 20일(금), 오전 10시30분 ~ 11시45분, 경제관 2층",
            kst(2026, 11, 20, 10, 30),
        ),
        ("일시 : 11/20(금), 14:00~16:00", kst(2026, 11, 20, 14, 0)),
        ("일시 : 11월 20일 (금), 오후 2시", kst(2026, 11, 20, 14, 0)),
        # A comma that only separates two items is still no time.
        ("일시 : 11/20(금), 경제관 2층", kst(2026, 11, 20, 23, 59)),
    ],
)
def test_a_comma_between_the_weekday_and_the_time(line, event):
    assert extract_from_body(line, POSTED).event_at == event
