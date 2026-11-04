"""Native recorder hook lifecycle check (KAN-576)."""

from __future__ import annotations

import os


def check_hook_lifecycle():
    """Install and remove production hooks without generating any input."""
    from dolphin_desktop._recorder import Recorder

    rec = Recorder(pid=os.getpid())
    try:
        rec.start()
        assert rec._hook_thread.is_alive()
        assert rec._hook_error is None
    finally:
        rec.stop()
        if rec._hook_thread is not None:
            rec._hook_thread.join(2)
            assert not rec._hook_thread.is_alive()
    assert rec._hook_error is None
