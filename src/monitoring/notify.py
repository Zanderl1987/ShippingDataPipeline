from __future__ import annotations

import json
import logging
import smtplib
from dataclasses import dataclass, field
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


@dataclass
class Notification:
    """A notification to send."""

    title: str
    message: str
    level: str = "info"  # info, warning, error, success
    source: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


class Notifier:
    """Send notifications through various channels."""

    def __init__(
        self,
        webhook_url: str | None = None,
        email_config: dict[str, str] | None = None,
        log_file: str | None = None,
    ) -> None:
        """Initialize notifier.

        Args:
            webhook_url: Slack/Discord webhook URL for alerts.
            email_config: Email settings (smtp_host, smtp_port, user,
                          password, from_addr, to_addrs).
            log_file: Path to log file for notifications.
        """
        self.webhook_url = webhook_url
        self.email_config = email_config
        self.log_file = log_file

    @classmethod
    def from_env(cls) -> Notifier:
        """Create notifier from environment variables."""
        from src.config import settings

        email_cfg: dict[str, str] | None = None
        if settings.smtp_host:
            email_cfg = {
                "smtp_host": settings.smtp_host or "",
                "smtp_port": str(settings.smtp_port or 587),
                "user": settings.smtp_user or "",
                "password": settings.smtp_password or "",
                "from_addr": settings.email_from or "",
                "to_addrs": ",".join(
                    settings.email_to.split(",") if settings.email_to else []
                ),
            }

        return cls(
            webhook_url=settings.slack_webhook_url
            or settings.discord_webhook_url,
            email_config=email_cfg,
            log_file=settings.notification_log_file,
        )

    def send(self, notification: Notification) -> dict[str, bool]:
        """Send notification through all configured channels.

        Returns:
            Dict of channel -> success status.
        """
        results: dict[str, bool] = {}

        # Always log
        self._log_notification(notification)
        results["log"] = True

        # Send webhook if configured
        if self.webhook_url:
            try:
                self._send_webhook(notification)
                results["webhook"] = True
            except Exception as e:
                logger.error("Webhook failed: %s", e)
                results["webhook"] = False

        # Send email if configured
        if self.email_config and self.email_config.get("smtp_host"):
            try:
                self._send_email(notification)
                results["email"] = True
            except Exception as e:
                logger.error("Email failed: %s", e)
                results["email"] = False

        return results

    def _log_notification(self, notification: Notification) -> None:
        """Log notification to file."""
        log_entry = {
            "timestamp": notification.timestamp.isoformat(),
            "level": notification.level,
            "title": notification.title,
            "message": notification.message,
            "source": notification.source,
            "metadata": notification.metadata,
        }

        log_line = json.dumps(log_entry)
        level_msg = notification.level.upper()

        if self.log_file:
            try:
                with open(self.log_file, "a") as f:
                    f.write(log_line + "\n")
            except Exception as e:
                logger.error("Failed to write to log file: %s", e)

        # Also log to Python logger
        log_method = getattr(logger, notification.level, logger.info)
        log_method("[%s] %s: %s", level_msg, notification.title, notification.message)

    def _send_webhook(self, notification: Notification) -> None:
        """Send to Slack/Discord webhook."""
        emoji_map = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "success": "✅",
        }
        emoji = emoji_map.get(notification.level, "📢")

        payload = {
            "text": f"{emoji} *{notification.title}*\n{notification.message}",
            "username": "ShippingDataPipeline",
        }

        data = json.dumps(payload).encode("utf-8")
        req = Request(
            self.webhook_url,  # type: ignore
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with urlopen(req, timeout=10) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Webhook returned {resp.status}")

    def _send_email(self, notification: Notification) -> None:
        """Send email notification."""
        if not self.email_config:
            return

        msg = MIMEMultipart()
        msg["From"] = self.email_config["from_addr"]
        msg["To"] = self.email_config["to_addrs"]
        msg["Subject"] = f"[SDP {notification.level.upper()}] {notification.title}"

        body = f"""
Shipping Data Pipeline Notification

Title: {notification.title}
Level: {notification.level.upper()}
Source: {notification.source or 'N/A'}
Time: {notification.timestamp:%Y-%m-%d %H:%M:%S}

Message:
{notification.message}

---
Metadata:
{json.dumps(notification.metadata, indent=2)}
"""
        msg.attach(MIMEText(body, "plain"))

        smtp_port = int(self.email_config.get("smtp_port", "587"))
        with smtplib.SMTP(
            self.email_config["smtp_host"],
            smtp_port,
        ) as server:
            if self.email_config["password"]:
                server.starttls()
                server.login(
                    self.email_config["user"],
                    self.email_config["password"],
                )
            server.send_message(msg)


def notify_collection_success(
    source: str,
    rows_fetched: int,
    rows_written: int,
    duration_ms: int,
) -> Notification:
    """Create a success notification for a collection."""
    return Notification(
        title=f"Collection Complete: {source}",
        message=(
            f"Successfully collected {rows_fetched:,} rows "
            f"({rows_written:,} written) in {duration_ms / 1000:.1f}s"
        ),
        level="success",
        source=source,
        metadata={
            "rows_fetched": rows_fetched,
            "rows_written": rows_written,
            "duration_ms": duration_ms,
        },
    )


def notify_collection_error(
    source: str,
    error: Exception,
) -> Notification:
    """Create an error notification for a failed collection."""
    return Notification(
        title=f"Collection Failed: {source}",
        message=f"Error: {error!s}",
        level="error",
        source=source,
        metadata={"error_type": type(error).__name__},
    )


def notify_quality_warning(
    warnings: list[str],
) -> Notification:
    """Create a warning notification for quality issues."""
    return Notification(
        title="Data Quality Warning",
        message="\n".join(f"- {w}" for w in warnings),
        level="warning",
        metadata={"warning_count": len(warnings)},
    )


def notify_stale_sources(
    stale_sources: list[tuple[str, str, float]],
) -> Notification:
    """Create a warning for stale sources.

    Args:
        stale_sources: List of (source, table, hours_stale) tuples.
    """
    lines = [f"- {s}: {h:.0f}h stale (in {t})" for s, t, h in stale_sources]
    return Notification(
        title="Stale Data Sources",
        message="\n".join(lines),
        level="warning",
        metadata={"stale_count": len(stale_sources)},
    )
