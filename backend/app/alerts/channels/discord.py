from __future__ import annotations

import httpx

from app.alerts.channels.base import (
    ChannelError,
    NotificationChannel,
    NotificationMessage,
    classify_http_status,
)

ALLOWED_PREFIXES = ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")


def is_valid_discord_webhook(url: str) -> bool:
    return url.startswith(ALLOWED_PREFIXES)


class DiscordChannel(NotificationChannel):
    name = "discord"

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def send(self, message: NotificationMessage, target: str) -> None:
        if not is_valid_discord_webhook(target):
            raise ChannelError("invalid discord webhook url", transient=False)
        embed = {
            "title": message.title[:250],
            "description": message.body[:2000],
            "url": message.app_url,
            "color": 0xF97316 if message.priority == "high" else 0x6366F1,
        }
        if message.image_url and message.image_url.startswith("https://"):
            embed["thumbnail"] = {"url": message.image_url}
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
                resp = await client.post(target, json={"username": "FlipFinder", "embeds": [embed]})
        except httpx.HTTPError as exc:
            raise ChannelError(f"discord network error: {type(exc).__name__}", transient=True) from exc
        if resp.status_code not in (200, 204):
            raise ChannelError(
                f"discord http {resp.status_code}", transient=classify_http_status(resp.status_code)
            )
