"""E-mail sign-up and sign-in: /api/v1/auth/*.

Everything that takes an e-mail address answers the same way whether or not the address is
registered, so the API cannot be used to find out who has an account.
"""

import logging
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import limits, messages
from app.auth.deps import (
    COOKIE_PATH,
    SESSION_COOKIE,
    Auth,
    get_app_settings,
    get_client_ip,
    get_limiter,
    get_mailer,
    optional_auth,
)
from app.auth.email_tokens import consume_email_token, issue_email_token
from app.auth.errors import AuthError
from app.auth.limiter import RateLimiter
from app.auth.mail import Mailer, MailError
from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.preferences import load_preferences
from app.auth.schemas import (
    Detail,
    EmailIn,
    LoginIn,
    LoginOut,
    RegisterIn,
    ResetIn,
    TokenIn,
    UserOut,
)
from app.auth.sessions import create_session, revoke_session, revoke_user_sessions
from app.auth.turnstile import verify_turnstile
from app.config import Settings
from app.db import get_session
from app.models import User
from app.timeutil import get_now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

Db = Annotated[Session, Depends(get_session)]
Cfg = Annotated[Settings, Depends(get_app_settings)]
Limiter = Annotated[RateLimiter, Depends(get_limiter)]
Mail = Annotated[Mailer, Depends(get_mailer)]
Ip = Annotated[str, Depends(get_client_ip)]
Now = Annotated[datetime, Depends(get_now)]

NATIVE_CLIENTS = ("android", "ios")
_DEV_WEB_URL = "http://localhost:3000"

MAIL_SENT = Detail(
    detail="요청을 받았습니다. 메일이 오지 않으면 스팸함을 확인하거나 잠시 후 다시 시도해 주세요."
)
INVALID_LINK = AuthError("유효하지 않거나 만료된 링크입니다.", "invalid_token", 400)


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        name=user.name,
        email_verified=user.email_verified_at is not None,
        has_password=user.password_hash is not None,
        google_linked=user.google_sub is not None,
        created_at=user.created_at,
        preferences=load_preferences(user.preferences),
    )


def web_link(settings: Settings, path: str, token: str | None = None) -> str:
    base = (settings.public_url or _DEV_WEB_URL).rstrip("/")
    return f"{base}{path}" + (f"?token={token}" if token else "")


def send_quietly(mailer: Mailer, to: str, message: tuple[str, str]) -> None:
    """Runs after the response. A mail failure must not change what the caller saw, or it
    would reveal which addresses are registered; the user can ask for the mail again."""
    try:
        mailer.send(to, *message)
    except MailError:
        logger.exception("could not send mail to a user")


def mail_limits(limiter: RateLimiter, email: str, ip: str) -> None:
    limits.enforce(limiter, f"mail:ip:{ip}", limits.MAIL_PER_IP)
    limits.enforce(limiter, f"mail:email:{email}", limits.MAIL_PER_EMAIL)


def client_kind(x_client: str | None) -> str:
    kind = (x_client or "web").strip().lower()
    if kind != "web" and kind not in NATIVE_CLIENTS:
        raise AuthError("지원하지 않는 클라이언트입니다.", "invalid_client", 400)
    return kind


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_days * 86400,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.app_env == "prod",
        samesite="lax",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.app_env == "prod",
        samesite="lax",
    )


@router.post("/register", status_code=202, response_model=Detail)
def register(
    body: RegisterIn,
    background: BackgroundTasks,
    db: Db,
    settings: Cfg,
    limiter: Limiter,
    mailer: Mail,
    ip: Ip,
    now: Now,
):
    limits.enforce(limiter, f"register:ip:{ip}", limits.REGISTER_PER_IP)
    mail_limits(limiter, body.email, ip)
    if not verify_turnstile(settings, body.turnstile_token, ip):
        raise AuthError("사람인지 확인하지 못했습니다. 다시 시도해 주세요.", "captcha_failed", 400)

    # Hash even if the address turns out to be taken, so every path costs the same time.
    password_hash = hash_password(body.password)
    outgoing: tuple[str, str] | None = None
    try:
        user = db.scalar(select(User).where(User.email == body.email))
        if user is not None and user.email_verified_at is not None:
            outgoing = messages.already_registered(
                web_link(settings, "/login"), web_link(settings, "/forgot-password")
            )
        else:
            if user is None:
                user = User(email=body.email, name=body.name)
                db.add(user)
            else:
                # Never verified: whoever registers last owns the pending password, so someone
                # who pre-registered a victim's address cannot keep their own password.
                user.name = body.name or user.name
            user.password_hash = password_hash
            db.flush()
            token = issue_email_token(db, user.id, "verify", now=now)
            outgoing = messages.verify_email(web_link(settings, "/verify-email", token))
        db.commit()
    except IntegrityError:
        db.rollback()  # two sign-ups for one address at once; the other one wins
        outgoing = None
    if outgoing:
        background.add_task(send_quietly, mailer, body.email, outgoing)
    return MAIL_SENT


@router.post("/verify-email", response_model=Detail)
def verify_email(body: TokenIn, db: Db, limiter: Limiter, ip: Ip, now: Now):
    limits.enforce(limiter, f"verify:ip:{ip}", limits.VERIFY_TOKEN_PER_IP)
    user_id = consume_email_token(db, body.token, "verify", now=now)
    if user_id is None:
        db.rollback()
        raise INVALID_LINK
    user = db.get(User, user_id)
    if user.email_verified_at is None:
        user.email_verified_at = now
    db.commit()
    return Detail(detail="이메일이 인증되었습니다. 이제 로그인할 수 있습니다.")


@router.post("/resend-verification", status_code=202, response_model=Detail)
def resend_verification(
    body: EmailIn,
    background: BackgroundTasks,
    db: Db,
    settings: Cfg,
    limiter: Limiter,
    mailer: Mail,
    ip: Ip,
    now: Now,
):
    mail_limits(limiter, body.email, ip)
    user = db.scalar(select(User).where(User.email == body.email))
    if user is not None and user.email_verified_at is None and user.password_hash is not None:
        token = issue_email_token(db, user.id, "verify", now=now)
        db.commit()
        message = messages.verify_email(web_link(settings, "/verify-email", token))
        background.add_task(send_quietly, mailer, body.email, message)
    return MAIL_SENT


@router.post("/login", response_model=LoginOut)
def login(
    body: LoginIn,
    response: Response,
    db: Db,
    settings: Cfg,
    limiter: Limiter,
    ip: Ip,
    now: Now,
    x_client: Annotated[str | None, Header()] = None,
    user_agent: Annotated[str | None, Header()] = None,
):
    kind = client_kind(x_client)
    k_pair = f"login:{body.email}:{ip}"
    k_email = f"login:email:{body.email}"
    k_ip = f"login:ip:{ip}"
    limits.enforce_not_blocked(limiter, k_pair, limits.LOGIN_FAIL_EMAIL_IP)
    limits.enforce_not_blocked(limiter, k_email, limits.LOGIN_FAIL_EMAIL)
    limits.enforce_not_blocked(limiter, k_ip, limits.LOGIN_FAIL_IP)

    user = db.scalar(select(User).where(User.email == body.email))
    stored = user.password_hash if user else None
    if not verify_password(stored, body.password):
        for key, (_, window) in (
            (k_pair, limits.LOGIN_FAIL_EMAIL_IP),
            (k_email, limits.LOGIN_FAIL_EMAIL),
            (k_ip, limits.LOGIN_FAIL_IP),
        ):
            limiter.record(key, window)
        raise AuthError("이메일 또는 비밀번호가 올바르지 않습니다.", "invalid_credentials", 401)
    limiter.reset(k_pair)

    if user.email_verified_at is None:
        raise AuthError(
            "이메일 인증이 필요합니다. 받은 메일의 링크를 열어 주세요.", "email_not_verified", 403
        )

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    token = create_session(
        db, user.id, now=now, ttl_days=settings.session_ttl_days, client=kind, user_agent=user_agent
    )
    db.commit()

    expires_at = now + timedelta(days=settings.session_ttl_days)
    if kind == "web":
        set_session_cookie(response, settings, token)
        return LoginOut(user=user_out(user), expires_at=expires_at)
    return LoginOut(user=user_out(user), token=token, expires_at=expires_at)


@router.post("/logout", status_code=204)
def logout(
    request_auth: Annotated[Auth | None, Depends(optional_auth)],
    response: Response,
    db: Db,
    settings: Cfg,
):
    """Always succeeds, so a client with a stale session can still clear its state."""
    if request_auth is not None:
        revoke_session(db, request_auth.session.token_hash)
        db.commit()
    clear_session_cookie(response, settings)
    response.status_code = 204
    return response


@router.post("/forgot-password", status_code=202, response_model=Detail)
def forgot_password(
    body: EmailIn,
    background: BackgroundTasks,
    db: Db,
    settings: Cfg,
    limiter: Limiter,
    mailer: Mail,
    ip: Ip,
    now: Now,
):
    mail_limits(limiter, body.email, ip)
    user = db.scalar(select(User).where(User.email == body.email))
    message = None
    if user is not None and user.email_verified_at is not None:
        token = issue_email_token(db, user.id, "reset", now=now)
        message = messages.reset_password(web_link(settings, "/reset-password", token))
    elif user is not None and user.password_hash is not None:
        # Never verified: a fresh verification link proves ownership just as well.
        token = issue_email_token(db, user.id, "verify", now=now)
        message = messages.verify_email(web_link(settings, "/verify-email", token))
    if message:
        db.commit()
        background.add_task(send_quietly, mailer, body.email, message)
    return MAIL_SENT


@router.post("/reset-password", response_model=Detail)
def reset_password(body: ResetIn, db: Db, limiter: Limiter, ip: Ip, now: Now):
    limits.enforce(limiter, f"reset:ip:{ip}", limits.RESET_PER_IP)
    user_id = consume_email_token(db, body.token, "reset", now=now)
    if user_id is None:
        db.rollback()
        raise INVALID_LINK
    user = db.get(User, user_id)
    user.password_hash = hash_password(body.password)
    if user.email_verified_at is None:
        user.email_verified_at = now
    revoke_user_sessions(db, user.id)  # whoever had the old password is signed out everywhere
    db.commit()
    return Detail(detail="비밀번호를 변경했습니다. 새 비밀번호로 로그인해 주세요.")
