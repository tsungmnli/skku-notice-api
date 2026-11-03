import logging
import smtplib

import pytest

from app.auth.mail import Mailer, MailError
from tests.conftest import make_settings


def test_without_smtp_host_it_logs_instead_of_sending(caplog):
    caplog.set_level(logging.INFO)
    Mailer(make_settings()).send("a@example.com", "제목", "https://x/verify?token=abc")
    assert "token=abc" in caplog.text


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.log, self.sent = host, port, [], []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self, context=None):
        self.log.append("starttls")

    def login(self, u, p):
        self.log.append(("login", u, p))

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture
def fake(monkeypatch):
    FakeSMTP.instances = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    return FakeSMTP


def smtp_settings(**kw):
    return make_settings(
        smtp_host="smtp.example.com", smtp_user="resend", smtp_password="key", **kw
    )


def test_sends_over_starttls_with_login(fake):
    Mailer(smtp_settings()).send("a@example.com", "안녕", "본문")
    (c,) = fake.instances
    assert (c.host, c.port) == ("smtp.example.com", 587)
    assert c.log == ["starttls", ("login", "resend", "key")]
    msg = c.sent[0]
    assert msg["To"] == "a@example.com"
    assert msg["From"] == "SKKU 공지 <noreply@skkunoti.tsungmn.com>"
    assert msg["Subject"] == "안녕"
    assert "본문" in msg.get_content()


def test_port_465_uses_implicit_tls(fake):
    Mailer(smtp_settings(smtp_port=465)).send("a@example.com", "s", "t")
    assert "starttls" not in fake.instances[0].log


def test_failures_surface_as_mail_error(monkeypatch):
    def boom(*a, **k):
        raise ConnectionRefusedError("down")

    monkeypatch.setattr(smtplib, "SMTP", boom)
    with pytest.raises(MailError):
        Mailer(smtp_settings()).send("a@example.com", "s", "t")


def test_header_injection_is_rejected(fake):
    with pytest.raises(ValueError):
        Mailer(smtp_settings()).send("a@example.com\nBcc: evil@example.com", "s", "t")
