"""Account timestamps are served as Asia/Seoul ISO strings, like the article ones."""

import uuid
from datetime import UTC, datetime

from app.auth.schemas import LoginOut, UserOut

# 2026-11-02 16:30 UTC == 2026-11-03 01:30 KST (the web shows the date, so the day must flip).
UTC_NOW = datetime(2026, 11, 2, 16, 30, tzinfo=UTC)


def _user() -> UserOut:
    return UserOut(
        id=uuid.uuid4(),
        email="a@b.co",
        name=None,
        email_verified=True,
        has_password=True,
        google_linked=False,
        created_at=UTC_NOW,
    )


def test_user_created_at_is_seoul_iso() -> None:
    assert _user().model_dump(mode="json")["created_at"] == "2026-11-03T01:30:00+09:00"


def test_login_expires_at_is_seoul_iso() -> None:
    out = LoginOut(user=_user(), expires_at=UTC_NOW).model_dump(mode="json")
    assert out["expires_at"] == "2026-11-03T01:30:00+09:00"
    assert out["user"]["created_at"] == "2026-11-03T01:30:00+09:00"
    assert out["token"] is None
