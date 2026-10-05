from __future__ import annotations

import asyncio
import json

from pywebpush import WebPushException, webpush

from app.alerts.channels.base import ChannelError, NotificationChannel, NotificationMessage
from app.core.config import Settings


class SubscriptionGone(ChannelError):
    """The browser subscription expired/unsubscribed (HTTP 404/410): delete it."""

    def __init__(self) -> None:
        super().__init__("push subscription gone", transient=False)


class WebPushChannel(NotificationChannel):
    name = "web_push"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.vapid_public_key and self.settings.vapid_private_key)

    async def send(self, message: NotificationMessage, target: str) -> None:
        """``target`` is the JSON-serialized PushSubscription (endpoint + keys)."""
        if not self.configured:
            raise ChannelError("web push not configured", transient=False)
        assert self.settings.vapid_private_key is not None
        payload = json.dumps(
            {
                "title": message.title,
                "body": message.body,
                "url": message.app_url,
                "priority": message.priority,
                "image": message.image_url,
            }
        )
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info=json.loads(target),
                data=payload,
                vapid_private_key=self.settings.vapid_private_key.get_secret_value(),
                vapid_claims={"sub": self.settings.vapid_subject},
                ttl=3600,
            )
        except WebPushException as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (404, 410):
                raise SubscriptionGone() from exc
            raise ChannelError(
                f"web push error {status}", transient=status is None or status >= 500 or status == 429
            ) from exc
