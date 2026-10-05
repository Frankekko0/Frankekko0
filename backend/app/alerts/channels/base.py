"""Notification channel contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationMessage:
    title: str
    body: str
    app_url: str  # deep link to the deal page in FlipFinder
    listing_url: str | None
    priority: str
    image_url: str | None = None


class ChannelError(Exception):
    """Delivery failure. ``transient`` failures are retried with exponential backoff."""

    def __init__(self, message: str, *, transient: bool) -> None:
        super().__init__(message)
        self.transient = transient


class NotificationChannel(ABC):
    name: str

    @abstractmethod
    async def send(self, message: NotificationMessage, target: str) -> None:
        """Deliver ``message`` to ``target`` (email address, chat id, webhook URL, subscription...)."""


def classify_http_status(status: int) -> bool:
    """True when an HTTP failure is worth retrying."""
    return status == 429 or status >= 500
