"""Notification channel abstraction (Phase 4).

The decision layer (career/pursue.py, notifications/policy.py) knows
nothing about how an alert is delivered; a channel knows nothing about
whether an opportunity is good. Email is the first implementation
(notifications/email_channel.py) — adding Slack/Discord/webhook/push
later means implementing this interface, not touching the policy or
content code. See docs/alerts.md.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from careers_os.notifications.content import AlertContent


@dataclass
class ChannelResult:
    success: bool
    error: Optional[str] = None


class NotificationChannel(ABC):
    name: str

    @abstractmethod
    def send(self, content: AlertContent) -> ChannelResult:
        """Deliver one alert. Must never raise — failures are reported via
        `ChannelResult(success=False, error=...)` so a delivery failure
        can never corrupt evaluation or be mistaken for a sent alert (see
        storage/repository.py::record_notification).
        """
