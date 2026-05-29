"""Dolphin structured logging — stdlib wrapper with secret redaction.

Three levels are used throughout dolphin:
  DEBUG  — per-action detail (element found, click sent, …)
  INFO   — test lifecycle events (launch, close, screenshot)
  ERROR  — unexpected failures before exceptions are raised

Configure via the ``DOLPHIN_LOG_LEVEL`` env var or
``dolphin_desktop.config(log_level=…)`` / ``--dolphin-log-level`` CLI option.
"""

from __future__ import annotations

import logging
import os
import re

# Patterns for secrets that should never appear in logs.
_SECRET_RE = re.compile(
    r"((?:password|passwd|token|secret|api[_\-]?key|auth)"
    r'["\s:=]+)([^\s,}"\']{4,})',
    re.IGNORECASE,
)


def _redact(text: str) -> str:
    return _SECRET_RE.sub(r"\1***", text)


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _redact(super().format(record))


def setup_logging(level: str | None = None) -> None:
    """Configure the ``dolphin`` root logger.  Idempotent — safe to call repeatedly."""
    logger = logging.getLogger("dolphin")
    if logger.handlers:
        return

    level_str = level or os.environ.get("DOLPHIN_LOG_LEVEL", "INFO")
    level_int = getattr(logging, level_str.upper(), logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(
        _RedactingFormatter(
            "%(asctime)s [dolphin] %(levelname)-5s %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    handler.setLevel(level_int)
    logger.setLevel(level_int)
    logger.addHandler(handler)
    logger.propagate = False


def get_logger(name: str = "dolphin") -> logging.Logger:
    """Return ``logging.getLogger("dolphin")`` or a named child logger."""
    if name == "dolphin":
        return logging.getLogger("dolphin")
    return logging.getLogger(f"dolphin_desktop.{name}")
