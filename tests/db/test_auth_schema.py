"""Schema-level guarantees of the account tables: constraints, defaults and cascades."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import EmailToken, User, UserSession

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
HASH_A = "a" * 64
HASH_B = "b" * 64


def add_user(s: Session, email: str = "a@example.com", **kw) -> User:
    user = User(email=email, **kw)
    s.add(user)
    s.flush()
    return user


def add_session(s: Session, user: User, token_hash: str = HASH_A, **kw) -> UserSession:
    row = UserSession(
        token_hash=token_hash, user_id=user.id, expires_at=NOW + timedelta(days=30), **kw
    )
    s.add(row)
    s.flush()
    return row


def add_token(
    s: Session, user: User, purpose: str = "verify", token_hash: str = HASH_A, **kw
) -> EmailToken:
    row = EmailToken(
        token_hash=token_hash,
        user_id=user.id,
        purpose=purpose,
        expires_at=NOW + timedelta(hours=24),
        **kw,
    )
    s.add(row)
    s.flush()
    return row


def test_user_id_is_generated_by_python_and_by_the_database(engine):
    with Session(engine) as s, s.begin():
        user = add_user(s)
        assert user.id is not None
        raw = s.scalar(text("INSERT INTO users (email) VALUES ('raw@example.com') RETURNING id"))
        assert raw is not None and raw != user.id


def test_email_is_unique(engine):
    with pytest.raises(IntegrityError), Session(engine) as s, s.begin():
        add_user(s)
        add_user(s)


def test_email_must_be_lowercase(engine):
    with pytest.raises(IntegrityError), Session(engine) as s, s.begin():
        add_user(s, email="Kim@Example.com")


def test_google_sub_is_unique_but_optional(engine):
    with Session(engine) as s, s.begin():
        add_user(s, "a@example.com")
        add_user(s, "b@example.com")  # several accounts without a google_sub are fine
    with pytest.raises(IntegrityError), Session(engine) as s, s.begin():
        add_user(s, "c@example.com", google_sub="g-1")
        add_user(s, "d@example.com", google_sub="g-1")


def test_deleting_a_user_removes_sessions_and_email_tokens(engine):
    with Session(engine) as s, s.begin():
        user = add_user(s)
        add_session(s, user)
        add_token(s, user)
        s.execute(delete(User).where(User.id == user.id))
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(UserSession)) == 0
        assert s.scalar(select(func.count()).select_from(EmailToken)) == 0


def test_session_client_defaults_to_web(engine):
    with Session(engine) as s, s.begin():
        add_session(s, add_user(s))
        assert s.scalar(select(UserSession.client)) == "web"


def test_session_client_must_be_known(engine):
    with pytest.raises(IntegrityError), Session(engine) as s, s.begin():
        add_session(s, add_user(s), client="blackberry")


def test_only_one_unused_token_per_user_and_purpose(engine):
    with pytest.raises(IntegrityError), Session(engine) as s, s.begin():
        user = add_user(s)
        add_token(s, user, "verify", HASH_A)
        add_token(s, user, "verify", HASH_B)


def test_a_new_token_is_allowed_once_the_old_one_is_used(engine):
    with Session(engine) as s, s.begin():
        user = add_user(s)
        add_token(s, user, "verify", HASH_A, used_at=NOW)
        add_token(s, user, "verify", HASH_B)
        add_token(s, user, "reset", "c" * 64)  # another purpose does not compete


def test_email_token_purpose_must_be_known(engine):
    with pytest.raises(IntegrityError), Session(engine) as s, s.begin():
        add_token(s, add_user(s), "login")
