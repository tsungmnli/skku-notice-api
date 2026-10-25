"""Asia/Seoul helpers. Korea has no DST, so a fixed +09:00 offset is exact (and needs no tzdata)."""

from datetime import date, datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def get_now() -> datetime:
    """FastAPI dependency for "now"; tests override it to pin the clock."""
    return now_kst()


def kst_day_start(d: date) -> datetime:
    """00:00 KST of the given date."""
    return datetime.combine(d, time.min, tzinfo=KST)


def kst_next_day_start(d: date) -> datetime | None:
    """00:00 KST of the following day (exclusive upper bound); None past the calendar's end."""
    if d == date.max:
        return None
    return kst_day_start(d + timedelta(days=1))
