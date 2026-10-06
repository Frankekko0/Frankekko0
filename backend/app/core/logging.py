"""Structured logging (structlog) with redaction of sensitive values.

Log events are JSON in production so they can be shipped to any log backend. A redaction
processor removes passwords, tokens, secrets, cookies and authorization headers wherever
they appear in the event dict (including nested dicts), so a careless ``log.info(..., **data)``
never leaks credentials.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import structlog

SENSITIVE_KEY_PATTERN = re.compile(
    r"(pass(word)?|secret|token|api[_-]?key|authorization|cookie|csrf|webhook|private[_-]?key|smtp_password)",
    re.IGNORECASE,
)
REDACTED = "[REDACTED]"
# Telegram bot tokens / bearer tokens accidentally embedded in strings (e.g. URLs in errors).
_INLINE_SECRET_PATTERNS = [
    re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{10,}"),
    re.compile(r"https://discord(?:app)?\.com/api/webhooks/\S+"),
]


def _scrub_string(value: str) -> str:
    for pattern in _INLINE_SECRET_PATTERNS:
        value = pattern.sub(REDACTED, value)
    return value


def redact(value: Any, depth: int = 0) -> Any:
    """Recursively redact sensitive keys and inline secrets."""
    if depth > 6:
        return value
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if isinstance(k, str) and SENSITIVE_KEY_PATTERN.search(k) else redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(redact(v, depth + 1) for v in value)
    if isinstance(value, str):
        return _scrub_string(value)
    return value


def _redaction_processor(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return redact(event_dict)  # type: ignore[no-any-return]


def configure_logging(
    level: str = "INFO", json_output: bool = True, error_log: str | None = None, process: str = "app"
) -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        _redaction_processor,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            *shared,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(foreign_pre_chain=shared, processor=renderer)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    if error_log:
        # Warnings and errors (already redacted) kept on disk for the error log page. A log
        # directory that cannot be written never stops the application.
        path = Path(error_log) / f"errors-{process}.jsonl"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        except OSError as exc:
            logging.getLogger(__name__).warning("error log not writable: %s (%s)", path, type(exc).__name__)
        else:
            file_handler.setLevel(logging.WARNING)
            file_handler.setFormatter(
                structlog.stdlib.ProcessorFormatter(
                    foreign_pre_chain=shared, processor=structlog.processors.JSONRenderer()
                )
            )
            root.addHandler(file_handler)
    root.setLevel(level.upper())
    # Keep noisy libraries quiet; uvicorn access logs are replaced by our request middleware.
    for noisy in ("uvicorn.access", "httpx", "httpcore", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
