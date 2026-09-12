"""Dolphin structured logging — stdlib wrapper with secret redaction.

Three levels are used throughout dolphin:
  DEBUG  — per-action detail (element found, click sent, …)
  INFO   — test lifecycle events (launch, close, screenshot)
  ERROR  — unexpected failures before exceptions are raised

Configure via the ``DOLPHIN_DESKTOP_LOG_LEVEL`` / ``DOLPHIN_LOG_LEVEL`` env
vars or ``dolphin_desktop.config(log_level=…)`` / ``--dolphin-desktop-log-level``
CLI option — all of them resolve through :mod:`dolphin_desktop._config`.

Every logger dolphin emits through lives under the ``dolphin_desktop``
namespace (``dolphin_desktop.selfheal``, ``dolphin_desktop.plugin``,
``dolphin_desktop.mainframe``, …).  Level filtering follows from that
hierarchy alone; redaction does not, because a filter on a parent logger
never runs for a child's records — :func:`install_redaction` therefore
attaches the redacting filter to each dolphin logger individually and hooks
the record factory so that children created with a bare ``logging.getLogger``
are covered too.
"""

from __future__ import annotations

import logging
import re
from typing import Any

# Secret redaction.
#
# This pattern runs over every dolphin log line AND over pytest's ``longrepr``,
# which is persisted to trace.db, rendered into trace.html and zipped into the
# Allure attachment. It therefore has two jobs that pull against each other:
# never print a credential, and never corrupt the failure text an engineer
# needs to read. The structure is deliberate:
#
# * The keyword may sit **inside** a compound identifier. Anchoring it to a
#   word boundary missed ``AWS_SECRET_ACCESS_KEY``, ``private_key`` and
#   ``password_hash`` entirely — the most canonical secret env var in
#   existence went to trace.db verbatim.
# * Any auth **scheme** is absorbed into the prefix, not just Bearer/Basic.
#   Matching only those two masked the scheme and printed the credential
#   after it, which looks redacted and is not.
# * A **quoted** value runs to its closing quote, so a passphrase with spaces
#   or a password containing a comma is masked whole.
# * A **bare** value stops at the delimiters of the serialisation formats we
#   care about, and excludes Python literals — masking those turned an
#   assertion diff into two identical sides. A comma only ends the value when
#   a space follows it, which is what separates a repr from ``P@ss,w0rd``.
#
# Known limit: an unquoted value containing a semicolon (``PWD=my;pass`` in a
# connection string) is masked only up to the semicolon, because there the
# semicolon really is the delimiter. Quote such values, or expect the tail to
# survive. Redaction is best-effort — see SECURITY.md.
_SECRET_RE = re.compile(
    r"("
    # No leading ``[A-Za-z0-9_.-]*`` here, deliberately. It looked necessary to
    # reach the keyword inside ``AWS_SECRET_ACCESS_KEY`` and was both useless
    # and dangerous: the engine already tries every start position, so the
    # match simply begins at ``SECRET`` and the prefix stays in the output
    # untouched — while the quantifier itself turned an ordinary long log line
    # into quadratic backtracking (2 MB took minutes).
    r"(?:password|passwd|passphrase|pwd|secret|token|api[_\-]?key|apikey"
    r"|private[_\-]?key|credential|authorization|auth(?!or)|signature"
    r"|sessionid|sas)"
    # Possessive. The suffix carries the rest of a compound name (``_ACCESS_KEY``
    # after ``SECRET``), and letting it backtrack made a long run of identifier
    # characters quadratic: 44 KB of them took 1.7 s, so a 500 KB traceback hung
    # for minutes. Nothing after the suffix can be an identifier character, so
    # giving back what it consumed could never produce a match anyway.
    # Bounded as well as possessive. Possessive alone still left the cost
    # quadratic — the keyword alternation is retried at every position, and
    # each hit rescanned the whole run — so 44 KB of identifier characters
    # (a base64 blob in a traceback) took half a second. 64 characters is far
    # longer than any real ``*_SECRET_ACCESS_KEY`` tail.
    r"[A-Za-z0-9_.\-]{0,64}+"
    # Not a source file. Matching the keyword inside an identifier is what
    # reaches AWS_SECRET_ACCESS_KEY, but it also reaches a *filename*, and
    # every traceback ends with "path.py:42: AssertionError" — so a test in
    # test_sap_auth.py had its failing line number replaced by the mask. The
    # line number is the single most useful token in the artifact.
    r"(?<!\.py)(?<!\.pyi)(?<!\.txt)(?<!\.log)(?<!\.json)(?<!\.yaml)(?<!\.yml)"
    r"(?<!\.ini)(?<!\.cfg)(?<!\.xml)(?<!\.html)(?<!\.js)(?<!\.ts)(?<!\.md)"
    r"[\"']?[ \t]*[:=][ \t]*"
    r"(?:(?:Bearer|Basic|Digest|Negotiate|NTLM|Token|SharedKey"
    r"|AWS4-HMAC-SHA256)[ \t]+)?"
    r")"
    r"(?:"
    r"\"(?P<dq>[^\"\n]+)\""
    r"|'(?P<sq>[^'\n]+)'"
    # ``(?![\s,;)}\]])`` rather than a delimiter class, so a literal at the end
    # of the string is excluded too — a log record has no trailing newline, so
    # ``credentials=None`` was masked while ``credentials=None, x=1`` was not.
    r"|(?P<bare>(?!(?:True|False|None|self|str|int|bool|float)(?![A-Za-z0-9_])"
    r"|[\s,;)}\]&])"
    r"(?:(?!,[ \t])[^\s;)}\]&\"'\n]){2,})"
    r")",
    re.IGNORECASE,
)

_CONNECTION_STRING_RE = re.compile(
    r"((?<![A-Za-z0-9_])connection[_\-]?string[ \t]*[:=][ \t]*)"
    r"(?:"
    r'"(?P<dq>[^"\n]+)"'
    r"|'(?P<sq>[^'\n]+)'"
    r"|(?P<bare>(?:(?!,[ \t])(?![}\]])(?!\))[^\r\n]){2,})"
    r")",
    re.IGNORECASE,
)

_ADDITIONAL_SECRET_RE = re.compile(
    r"((?<![A-Za-z0-9_])(?:login|username|user[_\-]?id|"
    r"connection[_\-]?string|clipboard)[ \t]*[:=][ \t]*)"
    r"(?:"
    r'"(?P<dq>[^"\n]+)"'
    r"|'(?P<sq>[^'\n]+)'"
    r"|(?P<bare>(?!(?:True|False|None|self|str|int|bool|float)(?![A-Za-z0-9_])"
    r"|[\s,;)}\]&])"
    r"(?:(?!,[ \t])[^\s;)}\]&\"'\n]){2,})"
    r")",
    re.IGNORECASE,
)

# Mainframe command payloads are sensitive even when the caller did not label
# them as ``password=...``.  A terminal String/SendKey value can be a login,
# password, or free-form user input, so redact the payload structurally.
_MAINFRAME_PAYLOAD_RE = re.compile(
    r"((?:String|SendKey)\(\s*[\"'])([^\"'\r\n]*)([\"']\s*\))",
    re.IGNORECASE,
)


def _mask(match: re.Match[str]) -> str:
    """Replace the value while preserving the quotes that delimited it."""
    prefix = match.group(1)
    if match.group("dq") is not None:
        return f'{prefix}"***"'
    if match.group("sq") is not None:
        return f"{prefix}'***'"
    return f"{prefix}***"


def _redact(text: str) -> str:
    text = _CONNECTION_STRING_RE.sub(_mask, text)
    text = _SECRET_RE.sub(_mask, text)
    text = _ADDITIONAL_SECRET_RE.sub(_mask, text)
    return _MAINFRAME_PAYLOAD_RE.sub(r"\1***\3", text)


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return _redact(super().format(record))


class _RedactingFilter(logging.Filter):
    """Redact the record in place, before any handler formats it.

    A formatter only protects the handlers dolphin installs itself.  Under
    pytest the handlers belong to the logging plugin (terminal, ``caplog``,
    JUnit XML), so the record has to be sanitised on the way out of the
    logger instead.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # A filter raising would propagate into the caller of log(); a
            # malformed format string is the handler's problem, as before.
            return True
        redacted = _redact(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


ROOT_LOGGER = "dolphin_desktop"

_REDACTING_FILTER = _RedactingFilter()

_own_factory: Any = None


def _install_record_factory() -> None:
    """Redact every ``dolphin_desktop`` record as it is created.

    The per-logger filter only reaches loggers that went through
    :func:`get_logger` and existed when the sweep last ran; a module reaching
    for ``logging.getLogger("dolphin_desktop.trace")`` directly bypasses both,
    and a parent's filter never runs for a child's record.  The record factory
    is the one hook every record passes through regardless.
    """
    global _own_factory
    previous = logging.getLogRecordFactory()
    if previous is _own_factory:
        return
    prefix = f"{ROOT_LOGGER}."

    def _factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        if record.name == ROOT_LOGGER or record.name.startswith(prefix):
            _REDACTING_FILTER.filter(record)
        return record

    _own_factory = _factory
    logging.setLogRecordFactory(_factory)


def install_redaction(logger: logging.Logger | None = None) -> None:
    """Attach the secret-redaction filter to *logger*.

    With no argument, installs the record-factory hook that covers the whole
    ``dolphin_desktop`` namespace and attaches the filter to the
    ``dolphin_desktop`` logger and to every child that exists so far.
    Idempotent.
    """
    if logger is not None:
        if _REDACTING_FILTER not in logger.filters:
            logger.addFilter(_REDACTING_FILTER)
        return

    _install_record_factory()
    install_redaction(logging.getLogger(ROOT_LOGGER))
    prefix = f"{ROOT_LOGGER}."
    for name in list(logging.Logger.manager.loggerDict):
        if name.startswith(prefix):
            install_redaction(logging.getLogger(name))


def _level_int(level: str | None) -> int:
    from ._config import VALID_LOG_LEVELS

    if level is None:
        from ._config import get_log_level

        level = get_log_level()
    normalized = level.upper()
    if normalized not in VALID_LOG_LEVELS:
        raise ValueError(
            f"Invalid log_level: {level!r}; expected one of {', '.join(VALID_LOG_LEVELS)}"
        )
    return getattr(logging, normalized)


def apply_log_level(level: str | None = None) -> None:
    """Set the level of the ``dolphin_desktop`` logger and of any handler it owns.

    Deliberately never installs a handler, so ``config(log_level=…)`` also
    takes effect under pytest, where the logging plugin owns the handlers and
    a dolphin-installed one would duplicate every line.
    """
    logger = logging.getLogger(ROOT_LOGGER)
    level_int = _level_int(level)
    logger.setLevel(level_int)
    for existing in logger.handlers:
        existing.setLevel(level_int)


def setup_logging(level: str | None = None) -> None:
    """Configure the ``dolphin_desktop`` root logger with a console handler.

    Idempotent — repeat calls never stack handlers, but they do apply the
    requested *level* so that a later ``config(log_level=…)`` takes effect.
    *level* defaults to the configured level, i.e. ``DOLPHIN_DESKTOP_LOG_LEVEL``
    or ``DOLPHIN_LOG_LEVEL``.
    """
    logger = logging.getLogger(ROOT_LOGGER)

    install_redaction()
    apply_log_level(level)
    if logger.handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(
        _RedactingFormatter(
            "%(asctime)s [dolphin] %(levelname)-5s %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    handler.setLevel(logger.level)
    logger.addHandler(handler)
    logger.propagate = False


def get_logger(name: str = ROOT_LOGGER) -> logging.Logger:
    """Return the ``dolphin_desktop`` root logger or a named child logger.

    ``get_logger("mainframe")``, ``get_logger("dolphin_desktop.mainframe")``
    and the legacy ``get_logger("dolphin")`` all resolve under the single
    ``dolphin_desktop`` root that :func:`setup_logging` configures.  The
    returned logger always carries the redaction filter.
    """
    if name in (ROOT_LOGGER, "dolphin"):
        logger = logging.getLogger(ROOT_LOGGER)
    elif name.startswith(f"{ROOT_LOGGER}."):
        logger = logging.getLogger(name)
    else:
        logger = logging.getLogger(f"{ROOT_LOGGER}.{name}")
    install_redaction(logger)
    return logger


install_redaction()
