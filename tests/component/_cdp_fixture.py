"""Controlled Chromium processes for the CDP endpoint security retest."""

from __future__ import annotations

import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from dolphin_desktop import Desktop
from dolphin_desktop._application import _enumerate_descendant_pids
from dolphin_desktop._netinfo import loopback_listener_pids


def chromium_executable() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        executable = playwright.chromium.executable_path
    assert Path(executable).is_file(), (
        "Install the test browser: python -m playwright install chromium"
    )
    return executable


def unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_until(predicate, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Listener did not close before the timeout"
        time.sleep(0.05)


def assert_port_owned_by_tree(port: int, pid: int) -> None:
    owners = loopback_listener_pids(port)
    allowed = {pid} | _enumerate_descendant_pids(pid)
    assert owners and owners <= allowed, f"Port {port}: owners {owners}, allowed {allowed}"


@contextmanager
def local_pages(port: int = 0):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = f"<title>{self.path}</title><h1>{self.path}</h1>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class ChromiumSession:
    """Keep each parallel worker's Playwright objects on their owning thread."""

    def __init__(
        self,
        executable: str,
        profile: Path,
        port: int,
        url: str,
        *,
        startup_timeout: float = 30,
    ) -> None:
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.app = None
        self.session = None
        command = subprocess.list2cmdline(
            [
                executable,
                "--headless=new",
                "--no-first-run",
                "--no-default-browser-check",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile}",
                url,
            ]
        )

        def launch() -> int:
            try:
                self.app, self.session = Desktop(hidden=False).launch_electron_cdp(
                    command, debug_port=port, timeout=20, startup_delay=0
                )
                pid = self.app.process_id
                # A live CDP target can still be loading its initial document.
                # Wait on the owning thread for this URL and its load event
                # before title, cookie and storage assertions use the session.
                self.session.wait_for_url(url, timeout=20)
                return pid
            except BaseException:
                self._cleanup()
                raise

        try:
            self.pid = self.executor.submit(launch).result(timeout=startup_timeout)
        except BaseException:
            # This task runs after launch, even if result() timed out before
            # the worker acquired the session. Never cancel the queued cleanup
            # or wait here for a stalled launch to finish.
            try:
                self.executor.submit(self._cleanup)
            finally:
                self.executor.shutdown(wait=False)
            raise

    def evaluate(self, script: str):
        return self.executor.submit(self.session.evaluate, script).result(timeout=10)

    def _cleanup(self) -> None:
        session, self.session = self.session, None
        app, self.app = self.app, None
        try:
            if session is not None:
                session.close()
        finally:
            if app is not None:
                app.kill()

    def close(self) -> None:
        try:
            self.executor.submit(self._cleanup).result(timeout=15)
        finally:
            self.executor.shutdown(wait=False)

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.close()
