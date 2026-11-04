"""A separate real IPC owner that can be killed without Python cleanup."""

from __future__ import annotations

import os
from multiprocessing.connection import Connection
from pathlib import Path

from dolphin_desktop._qt_inject import QtAgentClient


def park_controller(pid: int, version: str, state: str, channel: Connection, marker: str) -> None:
    agent = None
    try:
        agent = QtAgentClient.attach(pid, version)
        assert agent.ping() == "pong"
        if state == "disconnected":
            agent._close_pipe()  # Drop transport without calling the native stop export.
        elif state == "partial":
            agent._write_line(b'{"id":')  # Leave the server waiting for the rest of a frame.
        channel.send({"status": "ready", "pid": os.getpid(), "pipe_name": agent._pipe_name})
        channel.recv()  # The parent must TerminateProcess while we are parked here.
    except Exception as error:
        channel.send({"status": "error", "error": str(error)})
        raise
    finally:
        if agent is not None:
            agent.close()
        Path(marker).write_text("Python cleanup ran", encoding="utf-8")
        channel.close()
