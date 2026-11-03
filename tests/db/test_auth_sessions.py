"""Session lifecycle and the auth dependencies, against a real database."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import sessions
from app.auth.deps import Auth, current_auth, current_user, optional_auth
from app.auth.tokens import hash_token
from app.main import create_app
from app.models import User, UserSession
from app.timeutil import get_now
from tests.conftest import make_settings

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
TTL = 30


@pytest.fixture
def db(engine):
    with Session(engine) as s:
        yield s


def make_user(db: Session, email="a@example.com", verified=True) -> User:
    user = User(email=email, email_verified_at=NOW if verified else None)
    db.add(user)
    db.flush()
    return user


def count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(UserSession))


def test_create_stores_only_the_hash(db):
    user = make_user(db)
    token = sessions.create_session(
        db, user.id, now=NOW, ttl_days=TTL, user_agent="Mozilla/5.0\n(x)"
    )
    row = db.get(UserSession, hash_token(token))
    assert row is not None and row.token_hash != token
    assert row.expires_at == NOW + timedelta(days=TTL)
    assert row.client == "web"
    assert "\n" not in row.user_agent


def test_unknown_client_is_rejected(db):
    user = make_user(db)
    with pytest.raises(ValueError):
        sessions.create_session(db, user.id, now=NOW, ttl_days=TTL, client="toaster")


def test_resolve_finds_the_user(db):
    user = make_user(db)
    token = sessions.create_session(db, user.id, now=NOW, ttl_days=TTL)
    got, sess = sessions.resolve_session(db, token, now=NOW + timedelta(hours=1), ttl_days=TTL)
    assert got.id == user.id and sess.token_hash == hash_token(token)


def test_unknown_expired_and_unverified_are_refused(db):
    user = make_user(db)
    token = sessions.create_session(db, user.id, now=NOW, ttl_days=TTL)
    assert sessions.resolve_session(db, "nope", now=NOW, ttl_days=TTL) is None
    late = NOW + timedelta(days=TTL, seconds=1)
    assert sessions.resolve_session(db, token, now=late, ttl_days=TTL) is None

    other = make_user(db, "b@example.com", verified=False)
    t2 = sessions.create_session(db, other.id, now=NOW, ttl_days=TTL)
    assert sessions.resolve_session(db, t2, now=NOW, ttl_days=TTL) is None


def test_expiry_slides_at_most_once_a_day(db):
    user = make_user(db)
    token = sessions.create_session(db, user.id, now=NOW, ttl_days=TTL)

    soon = NOW + timedelta(hours=5)
    _, sess = sessions.resolve_session(db, token, now=soon, ttl_days=TTL)
    assert sess.last_used_at == NOW and sess.expires_at == NOW + timedelta(days=TTL)

    next_day = NOW + timedelta(days=1, minutes=1)
    _, sess = sessions.resolve_session(db, token, now=next_day, ttl_days=TTL)
    assert sess.last_used_at == next_day
    assert sess.expires_at == next_day + timedelta(days=TTL)


def test_revoke_one_and_all(db):
    user = make_user(db)
    t1 = sessions.create_session(db, user.id, now=NOW, ttl_days=TTL)
    sessions.create_session(db, user.id, now=NOW, ttl_days=TTL)
    sessions.create_session(db, user.id, now=NOW, ttl_days=TTL, client="android")
    other = make_user(db, "b@example.com")
    sessions.create_session(db, other.id, now=NOW, ttl_days=TTL)

    sessions.revoke_session(db, hash_token(t1))
    assert count(db) == 3
    assert sessions.revoke_user_sessions(db, user.id, except_hash=None) == 2
    assert count(db) == 1  # the other user's session is untouched


def test_revoke_all_can_keep_the_current_session(db):
    user = make_user(db)
    keep = sessions.create_session(db, user.id, now=NOW, ttl_days=TTL)
    sessions.create_session(db, user.id, now=NOW, ttl_days=TTL)
    assert sessions.revoke_user_sessions(db, user.id, except_hash=hash_token(keep)) == 1
    assert sessions.resolve_session(db, keep, now=NOW, ttl_days=TTL) is not None


def test_delete_expired(db):
    user = make_user(db)
    sessions.create_session(db, user.id, now=NOW - timedelta(days=40), ttl_days=TTL)
    sessions.create_session(db, user.id, now=NOW, ttl_days=TTL)
    assert sessions.delete_expired_sessions(db, now=NOW) == 1
    assert count(db) == 1


# --- dependencies, through a real request ---------------------------------------------------

probe = APIRouter()


@probe.get("/whoami")
def whoami(user: Annotated[User, Depends(current_user)]):
    return {"email": user.email}


@probe.get("/via")
def via(auth: Annotated[Auth, Depends(current_auth)]):
    return {"cookie": auth.via_cookie, "client": auth.session.client}


@probe.get("/maybe")
def maybe(auth: Annotated[Auth | None, Depends(optional_auth)]):
    return {"signed_in": auth is not None}


@pytest.fixture
def web(migrated_db_url, engine):
    app = create_app(make_settings(database_url=migrated_db_url))
    app.include_router(probe, prefix="/probe")
    app.dependency_overrides[get_now] = lambda: NOW
    with Session(engine) as s, s.begin():
        user = make_user(s)
        token = sessions.create_session(s, user.id, now=NOW, ttl_days=TTL, client="android")
    with TestClient(app) as client:
        client.token = token
        yield client


def test_no_credentials_is_401_in_the_api_error_shape(web):
    r = web.get("/probe/whoami")
    assert r.status_code == 401
    assert r.json() == {"detail": "로그인이 필요합니다.", "code": "not_authenticated"}
    assert web.get("/probe/maybe").json() == {"signed_in": False}


def test_cookie_authenticates_and_is_flagged_as_cookie(web):
    web.cookies.set("sid", web.token)
    assert web.get("/probe/whoami").json() == {"email": "a@example.com"}
    assert web.get("/probe/via").json() == {"cookie": True, "client": "android"}


def test_bearer_authenticates_and_is_not_flagged_as_cookie(web):
    r = web.get("/probe/via", headers={"Authorization": f"Bearer {web.token}"})
    assert r.json() == {"cookie": False, "client": "android"}
    assert (
        web.get("/probe/via", headers={"Authorization": f"bearer {web.token}"}).status_code == 200
    )


def test_bad_tokens_are_401(web):
    assert web.get("/probe/whoami", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert web.get("/probe/whoami", headers={"Authorization": "Bearer "}).status_code == 401
    web.cookies.set("sid", "nope")
    assert web.get("/probe/whoami").status_code == 401


def test_a_bad_bearer_is_not_rescued_by_a_valid_cookie(web):
    web.cookies.set("sid", web.token)
    r = web.get("/probe/whoami", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_each_app_gets_its_own_limiter_and_mailer(migrated_db_url):
    a = create_app(make_settings(database_url=migrated_db_url))
    b = create_app(make_settings(database_url=migrated_db_url))
    assert a.state.limiter is not b.state.limiter
    assert a.state.mailer is not b.state.mailer
