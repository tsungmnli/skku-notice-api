"""Outgoing mail over SMTP (Resend in production).

Without `SMTP_HOST` (dev/test) nothing is sent; the message is logged instead so a developer
can copy the link. Production refuses to boot without SMTP (see `Settings`).
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import Settings

logger = logging.getLogger(__name__)

TIMEOUT = 10  # seconds


class MailError(RuntimeError):
    """The message could not be handed to the mail server."""


class Mailer:
    """Installed as `app.state.mailer`; tests replace it with a fake that records messages."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def send(self, to: str, subject: str, text: str) -> None:
        s = self._s
        if not s.smtp_host:
            logger.info(
                "mail not sent (SMTP_HOST unset)\nto: %s\nsubject: %s\n\n%s", to, subject, text
            )
            return

        msg = EmailMessage()
        msg["From"] = s.smtp_from
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(text)

        context = ssl.create_default_context()
        try:
            if s.smtp_port == 465:  # implicit TLS
                with smtplib.SMTP_SSL(
                    s.smtp_host, s.smtp_port, timeout=TIMEOUT, context=context
                ) as c:
                    self._deliver(c, msg)
            else:
                with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=TIMEOUT) as c:
                    c.starttls(context=context)
                    self._deliver(c, msg)
        except (smtplib.SMTPException, OSError) as exc:
            raise MailError(f"SMTP delivery to {s.smtp_host} failed: {exc}") from exc

    def _deliver(self, conn: smtplib.SMTP, msg: EmailMessage) -> None:
        if self._s.smtp_user and self._s.smtp_password:
            conn.login(self._s.smtp_user, self._s.smtp_password)
        conn.send_message(msg)
