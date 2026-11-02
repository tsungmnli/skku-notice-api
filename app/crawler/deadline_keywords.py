"""Clue words for reading dates out of a notice body (see `deadline.extract_from_body`).

This is the only place to edit when a notice shows no D-day because its body used a word we do
not know yet: add the word to the right tuple. Spaces inside a word are optional when matching
(`접수 기간` matches `접수기간`), so list each word once, without spaces.
"""

# A date after one of these is the application deadline.
DEADLINE_CLUES = (
    "모집기간",
    "접수기간",
    "신청기간",
    "지원기간",
    "응모기간",
    "등록기간",
    "제출기간",
    "모집일정",
    "접수일정",
    "신청일정",
    "지원일정",
    "제출기한",
    "신청기한",
    "접수기한",
    "지원기한",
    # Two names for the same period in brackets: `접수(응모)기간 : 10/19 ~ 11/16`
    "접수(응모)기간",
    "응모(접수)기간",
    "접수(신청)기간",
    "신청(접수)기간",
    # Notices that put the deadline in brackets after the way to apply:
    # `참가방법 : 이메일 접수(~11.12.(목) 23시 59분)`
    "참가방법",
    "접수방법",
    "신청방법",
    "지원방법",
    "제출방법",
    "응모방법",
    "마감",  # also covers 마감일, 마감기한, 모집마감, 신청마감
)

# A date after one of these is when the event / work / exam takes place (not a deadline).
EVENT_CLUES = (
    "일시",
    "일정",
    "행사일",
    "개최일",
    "진행일",
    "교육일",
    "시험일",
    "면접일",
    "근무일",
    "개강일",
)

# Words that contain a clue but mean something else; blanked out before matching.
NOT_CLUES = (
    "일시적",
    "동일시",
    # A status mark the board adds by hand once a line is over: `(마감)충남대학교: 10.30까지`.
    # It says the line is closed, not when anything closes.
    "(마감)",
    "[마감]",
    "(마감됨)",
    "[마감됨]",
    "(마감완료)",
    "[마감완료]",
)
