"""End-to-end account scenarios against a real database, through the HTTP API.

Mail goes to a fake mailer that records messages, so a test can read the link out of a mail the
way a user would. The clock is pinned and can be moved forward.
"""

import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.auth import sessions
from app.main import create_app
from app.models import EmailToken, User, UserSession
from app.timeutil import KST, get_now
from tests.conftest import make_settings

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=KST)
WEB = "http://127.0.0.1:3000"  # in CORS_ORIGINS (see make_settings)
PASSWORD = "correct horse battery"
NEW_PASSWORD = "another long passphrase"
EMAIL = "kim@example.com"


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send(self, to: str, subject: str, text: str) -> None:
        self.sent.append((to, subject, text))

    def to(self, address: str) -> list[tuple[str, str, str]]:
        # The API normalizes addresses to lowercase, so mail goes to the lowercase form.
        return [m for m in self.sent if m[0] == address.strip().lower()]

    def link_token(self, address: str) -> str:
        """The token in the link of the latest mail to `address`."""
        mails = self.to(address)
        assert mails, f"no mail was sent to {address}"
        found = re.search(r"token=([A-Za-z0-9_\-]+)", mails[-1][2])
        assert found, "the mail has no token link"
        return found.group(1)


class Env:
    def __init__(self, client: TestClient, mailer: FakeMailer, clock: dict, app) -> None:
        self.client, self.mailer, self.clock, self.app = client, mailer, clock, app

    def advance(self, **delta) -> None:
        self.clock["now"] += timedelta(**delta)

    def post(self, path: str, json: dict | None = None, **kw):
        return self.client.post(f"/api/v1{path}", json=json, **kw)

    def register(self, email: str = EMAIL, password: str = PASSWORD, **kw):
        return self.post("/auth/register", {"email": email, "password": password}, **kw)

    def verify(self, email: str = EMAIL):
        return self.post("/auth/verify-email", {"token": self.mailer.link_token(email)})

    def login(self, email: str = EMAIL, password: str = PASSWORD, **kw):
        return self.post("/auth/login", {"email": email, "password": password}, **kw)

    def signed_up(self, email: str = EMAIL, password: str = PASSWORD) -> None:
        assert self.register(email, password).status_code == 202
        assert self.verify(email).status_code == 200

    def db_count(self, model) -> int:
        with self.app.state.sessionmaker() as db:
            return db.scalar(select(func.count()).select_from(model))


@pytest.fixture
def make_env(migrated_db_url):
    opened: list[TestClient] = []

    def factory(**settings) -> Env:
        settings.setdefault("public_url", "https://web.test")
        app = create_app(make_settings(database_url=migrated_db_url, **settings))
        mailer = FakeMailer()
        app.state.mailer = mailer
        clock = {"now": NOW}
        app.dependency_overrides[get_now] = lambda: clock["now"]
        # A browser always sends Origin on these requests; the CSRF check expects it.
        client = TestClient(app, headers={"Origin": WEB})
        client.__enter__()
        opened.append(client)
        return Env(client, mailer, clock, app)

    yield factory
    for c in opened:
        c.__exit__(None, None, None)


@pytest.fixture
def env(make_env) -> Env:
    return make_env()


# --- 1. sign up, verify, sign in, sign out ----------------------------------------------


def test_signup_verify_login_logout(env):
    r = env.register()
    assert r.status_code == 202
    ((to, subject, text),) = env.mailer.to(EMAIL)
    assert "인증" in subject and "https://web.test/verify-email?token=" in text

    # Not verified yet: the right password still does not get in.
    assert env.login().status_code == 403

    assert env.verify().status_code == 200
    r = env.login()
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["email"] == EMAIL and body["user"]["email_verified"] is True
    assert body["user"]["has_password"] is True and body["user"]["google_linked"] is False
    assert body["token"] is None  # the web gets a cookie, never a body token

    cookie = r.headers["set-cookie"].lower()
    assert "sid=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "path=/api" in cookie

    me = env.client.get("/api/v1/me")
    assert me.status_code == 200 and me.json()["email"] == EMAIL
    assert me.headers["cache-control"] == "no-store"

    assert env.post("/auth/logout").status_code == 204
    assert env.client.get("/api/v1/me").status_code == 401
    assert env.db_count(UserSession) == 0


def test_email_is_case_insensitive(env):
    env.signed_up("Kim@Example.com")
    assert env.login("kim@example.com").status_code == 200


# --- 2. refusals, and not revealing who has an account ----------------------------------


def test_unverified_login_is_refused_with_a_code(env):
    env.register()
    r = env.login()
    assert r.status_code == 403 and r.json()["code"] == "email_not_verified"
    assert "sid" not in env.client.cookies


def test_wrong_password_and_unknown_email_look_identical(env):
    env.signed_up()
    wrong = env.login(password="not the password")
    unknown = env.login(email="nobody@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert wrong.json()["code"] == "invalid_credentials"


def test_register_answers_the_same_for_new_and_registered_addresses(env):
    env.signed_up()
    fresh = env.register("new@example.com")
    taken = env.register(EMAIL, password="a different password")
    assert fresh.status_code == taken.status_code == 202
    assert fresh.json() == taken.json()
    # The owner of the taken address is told by mail, and their account is untouched.
    subjects = [s for _, s, _ in env.mailer.to(EMAIL)]
    assert any("이미 가입" in s for s in subjects)
    assert env.login().status_code == 200
    assert env.login(password="a different password").status_code == 401


def test_forgot_password_answers_the_same_for_known_and_unknown(env):
    env.signed_up()
    known = env.post("/auth/forgot-password", {"email": EMAIL})
    unknown = env.post("/auth/forgot-password", {"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert env.mailer.to("nobody@example.com") == []


def test_resend_verification_does_not_reveal_accounts(env):
    env.register()
    pending = env.post("/auth/resend-verification", {"email": EMAIL})
    unknown = env.post("/auth/resend-verification", {"email": "nobody@example.com"})
    assert pending.status_code == unknown.status_code == 202
    assert pending.json() == unknown.json()
    assert len(env.mailer.to(EMAIL)) == 2 and env.mailer.to("nobody@example.com") == []


def test_last_to_register_an_unverified_address_owns_its_password(env):
    env.register(password="squatter password")
    env.register(password=PASSWORD)  # the real owner, later
    env.verify()
    assert env.login(password="squatter password").status_code == 401
    assert env.login(password=PASSWORD).status_code == 200


# --- 3. one-time links, expiry, reset ---------------------------------------------------


def test_verify_link_works_once(env):
    env.register()
    token = env.mailer.link_token(EMAIL)
    assert env.post("/auth/verify-email", {"token": token}).status_code == 200
    again = env.post("/auth/verify-email", {"token": token})
    assert again.status_code == 400 and again.json()["code"] == "invalid_token"


def test_verify_link_expires_after_a_day(env):
    env.register()
    token = env.mailer.link_token(EMAIL)
    env.advance(hours=25)
    r = env.post("/auth/verify-email", {"token": token})
    assert r.status_code == 400 and r.json()["code"] == "invalid_token"
    assert env.login().status_code == 403  # still unverified


def test_asking_for_a_new_verification_link_kills_the_old_one(env):
    env.register()
    old = env.mailer.link_token(EMAIL)
    env.post("/auth/resend-verification", {"email": EMAIL})
    new = env.mailer.link_token(EMAIL)
    assert old != new
    assert env.post("/auth/verify-email", {"token": old}).status_code == 400
    assert env.post("/auth/verify-email", {"token": new}).status_code == 200


def test_unknown_or_wrong_purpose_token_is_refused(env):
    env.register()
    verify_token = env.mailer.link_token(EMAIL)
    assert env.post("/auth/verify-email", {"token": "nonsense"}).status_code == 400
    # A verification link must not work as a password-reset link.
    r = env.post("/auth/reset-password", {"token": verify_token, "password": NEW_PASSWORD})
    assert r.status_code == 400


def test_reset_password_signs_out_every_session(env):
    env.signed_up()
    web = env.login()
    assert web.status_code == 200
    app_login = env.login(headers={"X-Client": "android"})
    bearer = {"Authorization": f"Bearer {app_login.json()['token']}"}
    assert env.client.get("/api/v1/me", headers=bearer).status_code == 200
    assert env.db_count(UserSession) == 2

    env.post("/auth/forgot-password", {"email": EMAIL})
    token = env.mailer.link_token(EMAIL)
    r = env.post("/auth/reset-password", {"token": token, "password": NEW_PASSWORD})
    assert r.status_code == 200

    assert env.db_count(UserSession) == 0
    assert env.client.get("/api/v1/me").status_code == 401
    assert env.client.get("/api/v1/me", headers=bearer).status_code == 401
    assert env.login(password=PASSWORD).status_code == 401
    assert env.login(password=NEW_PASSWORD).status_code == 200

    # The link is spent.
    again = env.post("/auth/reset-password", {"token": token, "password": "yet another one"})
    assert again.status_code == 400


def test_reset_link_expires_after_an_hour(env):
    env.signed_up()
    env.post("/auth/forgot-password", {"email": EMAIL})
    token = env.mailer.link_token(EMAIL)
    env.advance(minutes=61)
    r = env.post("/auth/reset-password", {"token": token, "password": NEW_PASSWORD})
    assert r.status_code == 400
    assert env.login(password=PASSWORD).status_code == 200  # password unchanged


# --- native clients ----------------------------------------------------------------------


def test_native_client_gets_a_bearer_token_and_no_cookie(env):
    env.signed_up()
    r = env.login(headers={"X-Client": "ios"})
    assert r.status_code == 200 and r.json()["token"]
    assert "set-cookie" not in r.headers
    bearer = {"Authorization": f"Bearer {r.json()['token']}"}
    assert env.client.get("/api/v1/me", headers=bearer).status_code == 200
    assert env.post("/auth/logout", headers=bearer).status_code == 204
    assert env.client.get("/api/v1/me", headers=bearer).status_code == 401
    with env.app.state.sessionmaker() as db:
        assert db.scalar(select(func.count()).select_from(UserSession)) == 0


def test_unknown_client_kind_is_rejected(env):
    env.signed_up()
    r = env.login(headers={"X-Client": "native"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_client"


# --- 4. rate limits and X-Forwarded-For --------------------------------------------------


def _fail_logins(env, n: int, forwarded: str | None = None, email: str = EMAIL):
    last = None
    for _ in range(n):
        headers = {"X-Forwarded-For": forwarded} if forwarded else {}
        last = env.login(email=email, password="wrong password", headers=headers)
    return last


def test_login_is_blocked_after_ten_failures_from_one_address(make_env):
    env = make_env(trusted_proxy_hops=1)
    env.signed_up()
    assert _fail_logins(env, 10, "1.1.1.1").status_code == 401
    blocked = env.login(password=PASSWORD, headers={"X-Forwarded-For": "1.1.1.1"})
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


def test_a_stranger_cannot_lock_the_owner_out(make_env):
    env = make_env(trusted_proxy_hops=1)
    env.signed_up()
    _fail_logins(env, 10, "6.6.6.6")  # the attacker is blocked...
    assert env.login(password="x", headers={"X-Forwarded-For": "6.6.6.6"}).status_code == 429
    # ...but the owner, from their own address, gets in.
    ok = env.login(password=PASSWORD, headers={"X-Forwarded-For": "2.2.2.2"})
    assert ok.status_code == 200


def test_forged_forwarded_for_does_not_dodge_the_limit(make_env):
    """One proxy in front: only the rightmost entry (added by the proxy) is believed."""
    env = make_env(trusted_proxy_hops=1)
    env.signed_up()
    for i in range(10):
        # The client invents a new leftmost address every time; the proxy appends the real one.
        r = env.login(password="wrong", headers={"X-Forwarded-For": f"10.0.0.{i}, 7.7.7.7"})
        assert r.status_code == 401
    r = env.login(password="wrong", headers={"X-Forwarded-For": "10.0.0.99, 7.7.7.7"})
    assert r.status_code == 429


def test_forwarded_for_is_ignored_when_no_proxy_is_trusted(make_env):
    env = make_env(trusted_proxy_hops=0)
    env.signed_up()
    for i in range(10):
        assert _fail_logins(env, 1, f"10.0.0.{i}").status_code == 401
    assert _fail_logins(env, 1, "10.0.0.200").status_code == 429


def test_register_is_limited_per_address(env):
    codes = [env.register(f"user{i}@example.com").status_code for i in range(6)]
    assert codes[:5] == [202] * 5 and codes[5] == 429


def test_mail_is_limited_per_recipient(env):
    env.signed_up()
    # signed_up() already counted one for this address; the limit is 3 per hour.
    codes = [env.post("/auth/forgot-password", {"email": EMAIL}).status_code for _ in range(4)]
    assert codes == [202, 202, 429, 429]
    assert len(env.mailer.to(EMAIL)) == 3  # the register mail and two reset mails


# --- CSRF (Origin) -----------------------------------------------------------------------


def test_cookie_request_from_a_foreign_origin_is_refused(env):
    env.signed_up()
    assert env.login().status_code == 200
    r = env.post("/auth/logout", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["code"] == "origin_not_allowed"
    assert env.client.get("/api/v1/me").status_code == 200  # still signed in


def test_cookie_request_without_origin_is_refused(env):
    env.signed_up()
    assert env.login().status_code == 200
    stripped = TestClient(env.app)  # no default Origin header
    stripped.cookies.update(env.client.cookies)
    r = stripped.delete("/api/v1/me")
    assert r.status_code == 403 and r.json()["code"] == "origin_not_allowed"


# --- 5. deleting the account -------------------------------------------------------------


def _delete(env, password: str | None = None, **kw):
    body = {} if password is None else {"password": password}
    return env.client.request("DELETE", "/api/v1/me", json=body, **kw)


def test_delete_account_needs_the_password(env):
    env.signed_up()
    env.login()

    missing = _delete(env)
    wrong = _delete(env, "not the password")
    assert missing.status_code == wrong.status_code == 403
    assert wrong.json()["code"] == "invalid_credentials"
    assert env.client.get("/api/v1/me").status_code == 200  # nothing was deleted

    assert _delete(env, PASSWORD).status_code == 204
    assert env.client.get("/api/v1/me").status_code == 401
    assert env.db_count(User) == 0 and env.db_count(UserSession) == 0
    assert env.db_count(EmailToken) == 0  # tokens cascade with the account

    # The address is free again.
    env.signed_up()
    assert env.login().status_code == 200


def test_wrong_delete_passwords_are_limited(env):
    env.signed_up()
    env.login()
    codes = [_delete(env, "wrong").status_code for _ in range(6)]
    assert codes[:5] == [403] * 5 and codes[5] == 429
    assert _delete(env, PASSWORD).status_code == 429  # even the right one waits


def _google_only_session(env, *, created_at: datetime) -> dict:
    with env.app.state.sessionmaker() as db:
        user = User(email="g@example.com", google_sub="g-1", email_verified_at=NOW)
        db.add(user)
        db.flush()
        token = sessions.create_session(db, user.id, now=created_at, ttl_days=30)
        db.commit()
    return {"Authorization": f"Bearer {token}"}


def test_google_account_can_delete_right_after_signing_in(env):
    bearer = _google_only_session(env, created_at=NOW)
    me = env.client.get("/api/v1/me", headers=bearer).json()
    assert me["has_password"] is False and me["google_linked"] is True
    assert _delete(env, headers=bearer).status_code == 204
    assert env.db_count(User) == 0


def test_google_account_must_sign_in_again_to_delete_later(env):
    bearer = _google_only_session(env, created_at=NOW)
    env.advance(minutes=11)
    r = _delete(env, headers=bearer)
    assert r.status_code == 403 and r.json()["code"] == "reauth_required"
    assert env.db_count(User) == 1
