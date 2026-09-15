"""Email notification channel (Phase 4) — the first NotificationChannel.

Uses only the standard library (`smtplib`/`email`) — no new dependency —
and is written against generic SMTP (host/port/username/password/STARTTLS),
not anything Zoho-specific, so swapping providers later is a config
change, not a code change. See docs/alerts.md#email-configuration.

Security: credential values are read from the environment and handed to
`smtplib` directly. Nothing here ever logs, prints, or includes a
credential value in a `ChannelResult.error` message — SMTP exceptions
surface the *server's* response text, not the credentials that were sent.
"""

import logging
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional

from careers_os.notifications.channel import ChannelResult, NotificationChannel
from careers_os.notifications.content import AlertContent

logger = logging.getLogger("careers_os.notifications.email")


@dataclass
class EmailConfig:
    host: str
    port: int
    username: str
    password: str
    from_address: str
    to_address: str
    use_tls: bool = True


def load_email_config_from_env() -> Optional[EmailConfig]:
    """Returns None (not a raised error) when any required variable is
    missing — the pipeline degrades to "can't send email" rather than
    crashing; see docs/alerts.md#email-configuration for the full list.
    """
    host = os.environ.get("SMTP_HOST")
    port_raw = os.environ.get("SMTP_PORT")
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    from_address = os.environ.get("SMTP_FROM")
    to_address = os.environ.get("CAREERS_ALERT_EMAIL")

    if not all([host, port_raw, username, password, from_address, to_address]):
        return None

    try:
        port = int(port_raw)
    except ValueError:
        logger.warning("email_channel.invalid_smtp_port", extra={"port": port_raw})
        return None

    use_tls = os.environ.get("SMTP_USE_TLS", "true").strip().lower() not in ("false", "0", "no")

    return EmailConfig(
        host=host, port=port, username=username, password=password,
        from_address=from_address, to_address=to_address, use_tls=use_tls,
    )


class EmailNotificationChannel(NotificationChannel):
    name = "email"

    def __init__(self, config: EmailConfig) -> None:
        self.config = config

    @classmethod
    def from_env(cls) -> Optional["EmailNotificationChannel"]:
        config = load_email_config_from_env()
        return cls(config) if config else None

    def _build_message(self, content: AlertContent) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = content.subject
        message["From"] = self.config.from_address
        message["To"] = self.config.to_address
        message.set_content(content.to_plain_text())
        return message

    def send(self, content: AlertContent) -> ChannelResult:
        try:
            message = self._build_message(content)
            with smtplib.SMTP(self.config.host, self.config.port, timeout=15) as server:
                if self.config.use_tls:
                    server.starttls()
                server.login(self.config.username, self.config.password)
                server.send_message(message)
            logger.info("email_channel.sent", extra={"to": self.config.to_address})
            return ChannelResult(success=True)
        except Exception as exc:  # noqa: BLE001 - a delivery failure must never propagate
            logger.warning("email_channel.send_failed", extra={"error": str(exc)})
            return ChannelResult(success=False, error=str(exc))

    def send_test_email(self) -> ChannelResult:
        test_content = AlertContent(
            company="Careers OS", role="Notification Test", location="-", work_arrangement="-",
            compensation="-", source="careers-os", url="https://example.com",
            pursue="pursue", eligibility="eligible", qualification="strong",
            career_direction="strong", opportunity_value="strong",
            reasons=["This is a test of the Careers OS email notification channel."],
            watchouts=[],
        )
        message = EmailMessage()
        message["Subject"] = "Careers OS notification test"
        message["From"] = self.config.from_address
        message["To"] = self.config.to_address
        message.set_content(
            "This is a test email from Careers OS.\n\n"
            "If you're reading this, SMTP configuration, connectivity, authentication, "
            "and delivery are all working correctly.\n\n"
            f"Sample alert body would look like:\n\n{test_content.to_plain_text()}"
        )
        try:
            with smtplib.SMTP(self.config.host, self.config.port, timeout=15) as server:
                if self.config.use_tls:
                    server.starttls()
                server.login(self.config.username, self.config.password)
                server.send_message(message)
            return ChannelResult(success=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("email_channel.test_failed", extra={"error": str(exc)})
            return ChannelResult(success=False, error=str(exc))
