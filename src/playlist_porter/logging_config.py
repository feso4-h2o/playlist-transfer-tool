"""CLI logging configuration and secret redaction."""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

REDACTED = "[redacted]"

SECRET_KEY_PARTS = (
    "authorization",
    "client_secret",
    "cookie",
    "credential",
    "header",
    "oauth",
    "refresh_token",
    "secret",
    "session",
    "token",
)

SECRET_TEXT_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;]+"),
    re.compile(r"(?i)(client_secret\s*[:=]\s*)[^\s,;&]+"),
    re.compile(r"(?i)(access_token\s*[:=]\s*)[^\s,;&]+"),
    re.compile(r"(?i)(refresh_token\s*[:=]\s*)[^\s,;&]+"),
    re.compile(r"(?i)(cookie\s*[:=]\s*)[^\r\n]+"),
)


@dataclass(frozen=True)
class LoggingSetup:
    """Paths and flags from one logging configuration pass."""

    verbosity: int
    debug_log: bool
    log_path: Path | None = None


def configure_logging(
    *,
    verbosity: int = 0,
    debug_log: bool = False,
    log_dir: str | Path = "logs",
) -> LoggingSetup:
    """Configure loguru sinks for one CLI invocation."""

    logger.remove()
    logger.configure(patcher=_redact_record)

    if verbosity > 0:
        logger.add(
            sys.stderr,
            level="DEBUG" if verbosity >= 2 else "INFO",
            format="{time:HH:mm:ss} | {level} | {message} | {extra}",
            colorize=False,
        )

    log_path = None
    if debug_log:
        log_path = _debug_log_path(log_dir)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_path,
            level="DEBUG",
            format="{time:YYYY-MM-DDTHH:mm:ss.SSS} | {level} | {message} | {extra}",
            encoding="utf-8",
        )
        logger.info("debug log enabled", path=str(log_path))

    return LoggingSetup(verbosity=verbosity, debug_log=debug_log, log_path=log_path)


def redact(value: Any, *, key: str | None = None) -> Any:
    """Return a copy of value with secrets replaced by a stable marker."""

    if _is_secret_key(key):
        return REDACTED
    if isinstance(value, BaseException):
        return redact(str(value), key=key)
    if isinstance(value, str):
        redacted = value
        for pattern in SECRET_TEXT_PATTERNS:
            redacted = pattern.sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)
        return redacted
    if isinstance(value, Mapping):
        return {
            item_key: redact(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [redact(item) for item in value]
    return value


def _redact_record(record: dict[str, Any]) -> None:
    record["message"] = str(redact(record["message"]))
    record["extra"] = redact(record["extra"])
    if record["exception"] is not None:
        record["extra"]["exception"] = redact(record["exception"])


def _debug_log_path(log_dir: str | Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(log_dir) / f"playlist-porter-debug-{timestamp}.log"


def _is_secret_key(key: str | None) -> bool:
    if key is None:
        return False
    normalized = key.replace("-", "_").casefold()
    return any(part in normalized for part in SECRET_KEY_PARTS)


__all__ = ["LoggingSetup", "REDACTED", "configure_logging", "redact"]
