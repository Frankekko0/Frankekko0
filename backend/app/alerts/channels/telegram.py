from __future__ import annotations

import html

import httpx

from app.alerts.channels.base import (
    ChannelError,
    NotificationChannel,
    NotificationMessage,
    classify_http_status,
)


class TelegramChannel(NotificationChannel):
    name = "telegram"

    def __init__(self, bot_token: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self._transport = transport

    async def send(self, message: NotificationMessage, target: str) -> None:
        text = (
            f"<b>{html.escape(message.title)}</b>\n{html.escape(message.body)}\n\n"
            f'<a href="{html.escape(message.app_url)}">Apri analisi</a>'
            + (f' · <a href="{html.escape(message.listing_url)}">Annuncio</a>' if message.listing_url else "")
        )
        try:
            async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
                resp = await client.post(
                    self._url,
                    json={
                        "chat_id": target,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
        except httpx.HTTPError as exc:
            raise ChannelError(f"telegram network error: {type(exc).__name__}", transient=True) from exc
        if resp.status_code != 200:
            raise ChannelError(
                f"telegram http {resp.status_code}", transient=classify_http_status(resp.status_code)
            )
