from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.cleanup import purge_expired
from app.models import EmailToken, User, UserSession

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(engine):
    with Session(engine) as s:
        yield s


def test_purge_removes_only_dead_rows(db):
    user = User(email="a@example.com")
    db.add(user)
    db.flush()
    day = timedelta(days=1)
    db.add_all(
        [
            UserSession(token_hash="1" * 64, user_id=user.id, expires_at=NOW - day),
            UserSession(token_hash="2" * 64, user_id=user.id, expires_at=NOW + day),
            EmailToken(
                token_hash="a" * 64, user_id=user.id, purpose="verify", expires_at=NOW - day
            ),
            EmailToken(
                token_hash="b" * 64,
                user_id=user.id,
                purpose="verify",
                expires_at=NOW + day,
                used_at=NOW - day,
            ),
            EmailToken(token_hash="c" * 64, user_id=user.id, purpose="reset", expires_at=NOW + day),
        ]
    )
    db.flush()

    result = purge_expired(db, now=NOW)
    assert (result.sessions, result.email_tokens) == (1, 2)
    assert db.scalars(select(UserSession.token_hash)).all() == ["2" * 64]
    assert db.scalars(select(EmailToken.token_hash)).all() == ["c" * 64]
    assert db.scalar(select(func.count()).select_from(User)) == 1
