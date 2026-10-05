from __future__ import annotations

import html
from email.message import EmailMessage

import aiosmtplib

from app.alerts.channels.base import ChannelError, NotificationChannel, NotificationMessage
from app.core.config import Settings


class EmailChannel(NotificationChannel):
    name = "email"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send(self, message: NotificationMessage, target: str) -> None:
        s = self.settings
        if not s.smtp_host:
            raise ChannelError("smtp not configured", transient=False)
        msg = EmailMessage()
        msg["From"] = s.smtp_from
        msg["To"] = target
        msg["Subject"] = message.title[:180]
        msg.set_content(f"{message.body}\n\nApri l'analisi: {message.app_url}\n")
        listing = (
            f'<p><a href="{html.escape(message.listing_url)}">Vedi annuncio</a></p>'
            if message.listing_url
            else ""
        )
        msg.add_alternative(
            f"<h2 style='font-family:sans-serif'>{html.escape(message.title)}</h2>"
            f"<p style='font-family:sans-serif'>{html.escape(message.body)}</p>"
            f"<p><a href='{html.escape(message.app_url)}'>Apri l'analisi in FlipFinder</a></p>{listing}",
            subtype="html",
        )
        try:
            await aiosmtplib.send(
                msg,
                hostname=s.smtp_host,
                port=s.smtp_port,
                username=s.smtp_username,
                password=s.smtp_password.get_secret_value() if s.smtp_password else None,
                start_tls=s.smtp_starttls,
                timeout=15,
            )
        except aiosmtplib.SMTPRecipientsRefused as exc:
            raise ChannelError("recipient refused", transient=False) from exc
        except (aiosmtplib.SMTPException, OSError) as exc:
            raise ChannelError(f"smtp error: {type(exc).__name__}", transient=True) from exc
