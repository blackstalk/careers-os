from unittest.mock import MagicMock, patch

from careers_os.notifications.content import AlertContent
from careers_os.notifications.email_channel import (
    EmailConfig,
    EmailNotificationChannel,
    load_email_config_from_env,
)

_CONFIG = EmailConfig(
    host="smtp.example.com", port=587, username="user@example.com", password="not-a-real-secret",
    from_address="alerts@example.com", to_address="me@example.com", use_tls=True,
)


def _content() -> AlertContent:
    return AlertContent(
        company="Acme", role="Solutions Architect", location="Remote", work_arrangement="remote",
        compensation="$150/hr", source="greenhouse", url="https://example.com/job",
        pursue="strong_pursue", eligibility="eligible", qualification="strong",
        career_direction="strong", opportunity_value="strong",
        reasons=["Strong bridge toward target direction."], watchouts=[],
    )


class TestLoadConfigFromEnv:
    def test_missing_configuration_returns_none(self, monkeypatch):
        for var in ("SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM", "CAREERS_ALERT_EMAIL"):
            monkeypatch.delenv(var, raising=False)
        assert load_email_config_from_env() is None

    def test_partial_configuration_returns_none(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_PORT", "587")
        for var in ("SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM", "CAREERS_ALERT_EMAIL"):
            monkeypatch.delenv(var, raising=False)
        assert load_email_config_from_env() is None

    def test_complete_configuration_loads(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_PORT", "587")
        monkeypatch.setenv("SMTP_USERNAME", "user@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("SMTP_FROM", "alerts@example.com")
        monkeypatch.setenv("CAREERS_ALERT_EMAIL", "me@example.com")
        config = load_email_config_from_env()
        assert config is not None
        assert config.host == "smtp.example.com"
        assert config.port == 587
        assert config.use_tls is True

    def test_invalid_port_returns_none(self, monkeypatch):
        monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
        monkeypatch.setenv("SMTP_PORT", "not-a-number")
        monkeypatch.setenv("SMTP_USERNAME", "user@example.com")
        monkeypatch.setenv("SMTP_PASSWORD", "secret")
        monkeypatch.setenv("SMTP_FROM", "alerts@example.com")
        monkeypatch.setenv("CAREERS_ALERT_EMAIL", "me@example.com")
        assert load_email_config_from_env() is None


class TestSend:
    def test_successful_send_returns_success_result(self):
        channel = EmailNotificationChannel(_CONFIG)
        mock_server = MagicMock()
        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp.return_value.__enter__.return_value = mock_server
            result = channel.send(_content())
        assert result.success is True
        assert result.error is None
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with(_CONFIG.username, _CONFIG.password)
        mock_server.send_message.assert_called_once()

    def test_failed_send_returns_failure_result_not_an_exception(self):
        channel = EmailNotificationChannel(_CONFIG)
        with patch("smtplib.SMTP", side_effect=OSError("connection refused")):
            result = channel.send(_content())
        assert result.success is False
        assert result.error is not None

    def test_failed_login_returns_failure_result(self):
        channel = EmailNotificationChannel(_CONFIG)
        mock_server = MagicMock()
        mock_server.login.side_effect = Exception("535 Authentication failed")
        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp.return_value.__enter__.return_value = mock_server
            result = channel.send(_content())
        assert result.success is False

    def test_no_tls_skips_starttls(self):
        no_tls_config = EmailConfig(**{**_CONFIG.__dict__, "use_tls": False})
        channel = EmailNotificationChannel(no_tls_config)
        mock_server = MagicMock()
        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp.return_value.__enter__.return_value = mock_server
            channel.send(_content())
        mock_server.starttls.assert_not_called()


class TestNoRealNetworkCalls:
    def test_send_never_makes_a_real_connection_in_tests(self):
        # Sanity check that our mock actually intercepts smtplib.SMTP —
        # if this ever fails, some test above could attempt a real send.
        channel = EmailNotificationChannel(_CONFIG)
        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp.return_value.__enter__.return_value = MagicMock()
            channel.send(_content())
            mock_smtp.assert_called_once_with(_CONFIG.host, _CONFIG.port, timeout=15)


class TestTestEmail:
    def test_send_test_email_uses_fixed_subject(self):
        channel = EmailNotificationChannel(_CONFIG)
        mock_server = MagicMock()
        captured = {}

        def capture_send(msg):
            captured["subject"] = msg["Subject"]

        mock_server.send_message.side_effect = capture_send
        with patch("smtplib.SMTP") as mock_smtp:
            mock_smtp.return_value.__enter__.return_value = mock_server
            result = channel.send_test_email()
        assert result.success is True
        assert captured["subject"] == "Careers OS notification test"

    def test_send_test_email_failure_is_reported_not_raised(self):
        channel = EmailNotificationChannel(_CONFIG)
        with patch("smtplib.SMTP", side_effect=OSError("timeout")):
            result = channel.send_test_email()
        assert result.success is False
