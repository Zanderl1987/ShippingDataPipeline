from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from src.monitoring.notify import (
    Notification,
    Notifier,
    notify_collection_error,
    notify_collection_success,
    notify_quality_warning,
    notify_stale_sources,
)


class TestNotification:
    def test_notification_creation(self) -> None:
        """Test notification creation."""
        notif = Notification(
            title="Test Title",
            message="Test message",
            level="info",
        )
        assert notif.title == "Test Title"
        assert notif.message == "Test message"
        assert notif.level == "info"
        assert notif.timestamp is not None


class TestNotifier:
    def test_log_notification(self, tmp_path: Path) -> None:
        """Test logging to file."""
        log_file = tmp_path / "test.log"
        notifier = Notifier(log_file=str(log_file))

        notif = Notification(
            title="Test",
            message="Test message",
            level="info",
        )

        results = notifier.send(notif)
        assert results["log"] is True

        # Verify file was written
        assert log_file.exists()
        with open(log_file) as f:
            lines = f.readlines()
            assert len(lines) == 1
            data = json.loads(lines[0])
            assert data["title"] == "Test"
            assert data["level"] == "info"

    def test_webhook_notification(self) -> None:
        """Test webhook sending."""
        notifier = Notifier(webhook_url="https://example.com/webhook")

        with patch.object(notifier, "_send_webhook") as mock_webhook:
            mock_webhook.return_value = None
            notif = Notification(
                title="Test",
                message="Test message",
                level="info",
            )
            results = notifier.send(notif)
            assert results["webhook"] is True
            mock_webhook.assert_called_once()

    def test_email_notification(self) -> None:
        """Test email sending."""
        email_config = {
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "user": "test@example.com",
            "password": "password",
            "from_addr": "from@example.com",
            "to_addrs": ["to@example.com"],
        }
        notifier = Notifier(email_config=email_config)

        with patch.object(notifier, "_send_email") as mock_email:
            mock_email.return_value = None
            notif = Notification(
                title="Test",
                message="Test message",
                level="info",
            )
            results = notifier.send(notif)
            assert results["email"] is True
            mock_email.assert_called_once()

    def test_from_env(self) -> None:
        """Test creating notifier from env."""
        with patch("src.config.settings") as mock_settings:
            mock_settings.slack_webhook_url = "https://example.com/slack"
            mock_settings.discord_webhook_url = None
            mock_settings.smtp_host = None
            mock_settings.notification_log_file = "/tmp/test.log"

            notifier = Notifier.from_env()
            assert notifier.webhook_url == "https://example.com/slack"


class TestNotificationHelpers:
    def test_notify_collection_success(self) -> None:
        """Test success notification helper."""
        notif = notify_collection_success(
            source="test_source",
            rows_fetched=100,
            rows_written=95,
            duration_ms=1500,
        )
        assert notif.level == "success"
        assert "test_source" in notif.title
        assert "100" in notif.message

    def test_notify_collection_error(self) -> None:
        """Test error notification helper."""
        error = ValueError("Test error")
        notif = notify_collection_error(
            source="test_source",
            error=error,
        )
        assert notif.level == "error"
        assert "test_source" in notif.title
        assert "Test error" in notif.message

    def test_notify_quality_warning(self) -> None:
        """Test quality warning notification."""
        warnings = ["Warning 1", "Warning 2"]
        notif = notify_quality_warning(warnings)
        assert notif.level == "warning"
        assert "Warning 1" in notif.message
        assert "Warning 2" in notif.message

    def test_notify_stale_sources(self) -> None:
        """Test stale sources notification."""
        stale = [("source1", "table1", 48.0), ("source2", "table2", 72.0)]
        notif = notify_stale_sources(stale)
        assert notif.level == "warning"
        assert "source1" in notif.message
        assert "source2" in notif.message


def test_push_levels_limit_webhook_but_not_log() -> None:
    notifier = Notifier(webhook_url="https://example.com/webhook", push_levels=("warning",))
    with patch.object(notifier, "_send_webhook") as mock_webhook:
        assert notifier.send(Notification(title="ok", message="m", level="success")) == {
            "log": True
        }
        mock_webhook.assert_not_called()
        notifier.send(Notification(title="bad", message="m", level="warning"))
        mock_webhook.assert_called_once()
