"""How a Google identity is matched to an account (the part that needs care)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import sessions
from app.auth.errors import AuthError
from app.auth.google import upsert_google_user
from app.models import User, UserSession

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(engine):
    with Session(engine) as s:
        yield s


def upsert(db, sub="g1", email="a@example.com", name="Kim"):
    return upsert_google_user(db, sub=sub, email=email, name=name, now=NOW)


def test_new_google_user_is_verified_and_has_no_password(db):
    u = upsert(db, email=" A@Example.com ")
    assert u.email == "a@example.com" and u.google_sub == "g1" and u.name == "Kim"
    assert u.email_verified_at == NOW and u.password_hash is None


def test_known_sub_returns_the_same_account_even_if_the_email_changed(db):
    first = upsert(db)
    again = upsert(db, email="new@example.com")
    assert again.id == first.id and again.email == "a@example.com"


def test_verified_password_account_is_linked_and_keeps_its_password(db):
    db.add(User(email="a@example.com", password_hash="hash", email_verified_at=NOW, name="Old"))
    db.flush()
    u = upsert(db)
    assert u.google_sub == "g1" and u.password_hash == "hash" and u.name == "Old"
    assert db.scalar(select(func.count()).select_from(User)) == 1


def test_unverified_account_loses_password_and_sessions_when_google_claims_it(db):
    squatter = User(email="a@example.com", password_hash="attacker-hash")
    db.add(squatter)
    db.flush()
    sessions.create_session(db, squatter.id, now=NOW, ttl_days=30)

    u = upsert(db)
    assert u.id == squatter.id
    assert u.password_hash is None and u.email_verified_at == NOW and u.google_sub == "g1"
    assert db.scalar(select(func.count()).select_from(UserSession)) == 0


def test_email_owned_by_another_google_account_is_a_conflict(db):
    upsert(db, sub="g1")
    with pytest.raises(AuthError) as exc:
        upsert(db, sub="g2")
    assert exc.value.code == "google_conflict"


def test_long_names_are_trimmed(db):
    assert len(upsert(db, name="x" * 300).name) == 100
    assert upsert(db, sub="g2", email="b@example.com", name="  ").name is None
