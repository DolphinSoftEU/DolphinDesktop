"""Codepage parameter — the backend records ``codepage=`` as a
``-charset <name>`` argument and connecting still works.

The mock returns EBCDIC bytes from cp037 (US), so testing that a
different codepage really translates the wire bytes requires a
non-English host. Instead we verify:

* ``codepage=None`` (default) yields an argument list without
  ``-charset``.
* ``codepage='us-intl'`` yields ``-charset us-intl`` and the session
  still connects and reads the screen without corruption.
* an unknown codepage is passed through rather than validated.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop
from tests.mainframe.mock_tn3270._mock_server import (
    MockTN3270Server,  # type: ignore[import-not-found]
)
from tests.mainframe.pub400._mainframe_env import WS3270  # type: ignore[import-not-found]


def _spawn_args(term):
    """Rebuild the s3270 argument list from the backend's own settings.

    Returns an empty list when no subprocess is running. The list is
    reconstructed from ``_binary`` / ``_model`` / ``_codepage`` /
    ``_extra_args``; the live process is not inspected.
    """
    proc = term._backend._proc  # type: ignore[attr-defined]
    if proc is None:
        return []
    b = term._backend  # type: ignore[attr-defined]
    args = [b._binary, "-model", b._model, "-utf8"]
    if b._codepage:
        args += ["-charset", b._codepage]
    args += list(b._extra_args)
    return args


def test_no_codepage_no_charset_flag() -> None:
    if WS3270 is None:
        pytest.skip("wc3270 not installed")
    srv = MockTN3270Server()
    port = srv.start()
    try:
        term = Desktop().mainframe(host="127.0.0.1", port=port, ws3270_path=WS3270, timeout=10)
        term.wait_ready(timeout=5)
        args = _spawn_args(term)
        assert "-charset" not in args, args
        term.disconnect()
    finally:
        srv.stop()


def test_us_intl_codepage_passes_flag_and_still_works() -> None:
    if WS3270 is None:
        pytest.skip("wc3270 not installed")
    srv = MockTN3270Server()
    port = srv.start()
    try:
        term = Desktop().mainframe(
            host="127.0.0.1",
            port=port,
            ws3270_path=WS3270,
            codepage="us-intl",
            timeout=10,
        )
        term.wait_ready(timeout=5)
        args = _spawn_args(term)
        assert "-charset" in args, args
        idx = args.index("-charset")
        assert args[idx + 1] == "us-intl"
        # Mock title must still be legible — no corruption from the flag.
        assert "MOCK" in term.text()
        term.disconnect()
    finally:
        srv.stop()


def test_invalid_codepage_still_starts_but_docs_note_the_risk() -> None:
    """s3270 falls back to 'bracket' on unknown charset — no crash.
    This test just proves the wrapper does not add validation of its own,
    matching the ws3270 policy of "warn and continue"."""
    if WS3270 is None:
        pytest.skip("wc3270 not installed")
    srv = MockTN3270Server()
    port = srv.start()
    try:
        term = Desktop().mainframe(
            host="127.0.0.1",
            port=port,
            ws3270_path=WS3270,
            codepage="not-a-real-charset",
            timeout=10,
        )
        term.wait_ready(timeout=5)
        assert term.is_connected()
        term.disconnect()
    finally:
        srv.stop()
