"""Read a local TN3270 server through the real ws3270 emulator.

Run from this source checkout: the server is shared with the regression tests.
No external host, account or certificate-store changes are needed. This local
demo does not exercise TLS or an enterprise mainframe.
"""

import os
import shutil

import pytest

from dolphin_desktop import Desktop

pytestmark = pytest.mark.integration


def test_read_local_terminal_screen():
    from tests.mainframe.mock_tn3270._mock_server import MockTN3270Server

    executable = os.environ.get("DOLPHIN_WS3270_PATH") or shutil.which("ws3270")
    if not executable:
        pytest.skip("Install wc3270 and put ws3270.exe on PATH or set DOLPHIN_WS3270_PATH")
    expected = "DOLPHIN LOCAL TN3270"
    with MockTN3270Server(title=expected) as server:
        with Desktop().mainframe(
            host="127.0.0.1", port=server.port, backend="s3270", ws3270_path=executable
        ) as terminal:
            terminal.wait_for_text(expected, timeout=10)
            assert terminal.screen().contains(expected)
