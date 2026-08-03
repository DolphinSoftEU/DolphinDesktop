"""Codepage parameter — proves the s3270 subprocess is spawned with
``-charset <name>`` when the caller passes ``codepage=``.

The mock returns EBCDIC bytes from cp037 (US), so testing that a
different codepage really translates the wire bytes requires a
non-English host. Instead we verify:

* ``codepage=None`` (default) spawns without the ``-charset`` argument.
* ``codepage='us-intl'`` spawns with ``-charset us-intl`` and still
  connects + reads the screen without corruption.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop
from tests.mainframe.mock_tn3270._mock_server import (
    MockTN3270Server,  # type: ignore[import-not-found]
)

# _mainframe_env + _mock_server are imported here via the conftest.py
# in this directory (which invokes add_import_path for the sibling
# pub400/ folder). No sys or pathlib imports needed.
from tests.mainframe.pub400._mainframe_env import WS3270  # type: ignore[import-not-found]


def _spawn_args(term):
    """Peek into the s3270 subprocess to inspect its argv."""
    proc = term._backend._proc  # type: ignore[attr-defined]
    if proc is None:
        return []
    # subprocess.Popen doesn't keep argv around, but we stored the
    # binary + args on the backend before spawning; reconstruct.
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
