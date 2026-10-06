"""Read Vinted notification emails from a mailbox (optional).

Read-only: the folder is opened with ``readonly=True`` and messages are fetched with
``BODY.PEEK[]``, so nothing is marked as read, moved or deleted. Credentials come only from the
environment (``IMAP_HOST``, ``IMAP_USER``, ``IMAP_PASSWORD``); a dedicated app password or a
filtered sub-folder is recommended. Only messages whose sender mentions Vinted are fetched.
"""

from __future__ import annotations

import asyncio
import imaplib
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.acquisition.service import EmailSummary, process_email_bytes
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import SystemState

log = get_logger(__name__)
STATE_KEY = "email_import"
MAX_PER_RUN = 200


def _fetch_new(last_uid: int, last_validity: int | None) -> tuple[int, int, list[bytes]]:
    s = get_settings()
    assert s.imap_host and s.imap_user and s.imap_password
    with imaplib.IMAP4_SSL(s.imap_host, s.imap_port, timeout=30) as box:
        box.login(s.imap_user, s.imap_password.get_secret_value())
        typ, _ = box.select(f'"{s.imap_folder}"', readonly=True)
        if typ != "OK":
            raise RuntimeError(f"cartella IMAP non trovata: {s.imap_folder}")
        validity = int((box.response("UIDVALIDITY")[1] or [b"0"])[0] or 0)
        if last_validity is not None and validity != last_validity:
            last_uid = 0  # the mailbox was rebuilt: UIDs restart
        typ, data = box.uid("search", None, f'(FROM "vinted" UID {last_uid + 1}:*)')
        uids = sorted(int(x) for x in (data[0] or b"").split() if int(x) > last_uid)[-MAX_PER_RUN:]
        messages: list[bytes] = []
        for uid in uids:
            typ, parts = box.uid("fetch", str(uid), "(BODY.PEEK[])")
            if typ == "OK" and parts and isinstance(parts[0], tuple):
                messages.append(parts[0][1])
        return (uids[-1] if uids else last_uid), validity, messages


async def poll_mailbox(session: AsyncSession) -> EmailSummary | None:
    s = get_settings()
    if not s.email_import_enabled:
        return None
    state = await session.get(SystemState, STATE_KEY)
    value = dict(state.value) if state else {}
    try:
        last_uid, validity, messages = await asyncio.to_thread(
            _fetch_new, int(value.get("last_uid", 0)), value.get("uidvalidity")
        )
    except (OSError, imaplib.IMAP4.error, RuntimeError) as exc:
        # Never log the password; the error text from imaplib does not contain it.
        log.warning("email.mailbox_unreachable", host=s.imap_host, error=str(exc)[:200])
        value |= {
            "last_error": f"Casella non raggiungibile: {str(exc)[:160]}",
            "last_run": datetime.now(UTC).isoformat(),
        }
        await _save(session, value)
        return None
    summary = await process_email_bytes(session, messages)
    value |= {
        "last_uid": last_uid,
        "uidvalidity": validity,
        "last_run": datetime.now(UTC).isoformat(),
        "last_error": None,
        "last_summary": summary.as_dict(),
    }
    await _save(session, value)
    log.info("email.polled", **summary.as_dict())
    return summary


async def _save(session: AsyncSession, value: dict[str, object]) -> None:
    stmt = pg_insert(SystemState).values(key=STATE_KEY, value=value)
    await session.execute(
        stmt.on_conflict_do_update(index_elements=["key"], set_={"value": stmt.excluded.value})
    )
