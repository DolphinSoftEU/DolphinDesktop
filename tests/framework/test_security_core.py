"""Security regression tests spanning the non-mainframe findings.

* KAN-469 — secrets are redacted at every write boundary (trace.db, crash ZIP,
  Allure text, structural redaction of dicts/UIA trees).
* KAN-470 — the bundled Qt agent DLL is hash-verified, and the agent pipe name
  is unguessable.
* KAN-471 — CDP screenshot bytes are decoded only as PNG/JPEG, size-bounded.
* KAN-472 — process termination is guarded by a (PID, creation-time) identity.
* KAN-473 — native libraries load only from absolute, trusted paths.
* KAN-475 — a CDP endpoint must be owned by the launched process tree.
* KAN-476 — launch_qt never mutates the parent os.environ, even concurrently.
* KAN-477 — http_ok only probes http/https URLs with a host.
"""

from __future__ import annotations

import io
import os
import sys
import types
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

# --------------------------------------------------------------------------- #
# KAN-469 — redaction at write boundaries                                      #
# --------------------------------------------------------------------------- #

_CANARY = "SYNTHETIC_CANARY_9f3a2b"


def test_structural_redaction_masks_by_key_and_value() -> None:
    from dolphin_desktop._logging import redact_value

    out = redact_value(
        {
            "password": _CANARY,
            "pin": 1234,
            "nested": {"token": _CANARY, "keep": "visible"},
            "list": [f"password={_CANARY}", "plain"],
        }
    )
    assert out["password"] == "***"
    assert out["pin"] == "***"
    assert out["nested"]["token"] == "***"
    assert out["nested"]["keep"] == "visible"
    assert _CANARY not in repr(out)


def test_trace_db_never_stores_a_secret(tmp_path: Path) -> None:
    from dolphin_desktop import _trace

    session = _trace.TraceSession("test::node", tmp_path / "run", mode="always")
    # A selector and an error message that both carry a canary.
    session.record_step(
        "type_text",
        selector=f"{{'password': '{_CANARY}'}}",
        error=f"failed with token={_CANARY}",
    )
    session.finish("failed", error_message=f"assert password = '{_CANARY}'")
    blob = (tmp_path / "run" / "trace.db").read_bytes()
    assert _CANARY.encode() not in blob


def test_crash_dump_zip_never_stores_a_secret(tmp_path: Path) -> None:
    from dolphin_desktop._crash import write_crash_dump

    try:
        raise ValueError(f"boom token={_CANARY}")
    except ValueError as exc:
        path = write_crash_dump(exc=exc, output_dir=tmp_path, extra={"password": _CANARY})

    with zipfile.ZipFile(path) as zf:
        for name in zf.namelist():
            assert _CANARY.encode() not in zf.read(name), f"{_CANARY} leaked into {name}"


def test_allure_text_attachment_is_redacted(monkeypatch) -> None:
    import dolphin_desktop.pytest_plugin as plugin

    captured: list[str] = []
    fake_allure = types.SimpleNamespace(
        attach=lambda content, name, attachment_type: captured.append(content),
        attachment_type=types.SimpleNamespace(TEXT="text"),
    )
    monkeypatch.setitem(sys.modules, "allure", fake_allure)
    plugin._attach_allure_text(f"stdout had password={_CANARY}", "stdout")
    assert captured and _CANARY not in captured[0]
    assert "***" in captured[0]


# --------------------------------------------------------------------------- #
# KAN-470 — Qt agent DLL integrity + pipe naming                               #
# --------------------------------------------------------------------------- #


def test_bundled_agent_dlls_match_their_manifest() -> None:
    from dolphin_desktop import _qt_agent

    for version in ("5", "6"):
        # verify=True is the default; a mismatch would raise here.
        path = _qt_agent.agent_dll_for(version)
        _qt_agent.verify_agent_dll(path)


def test_a_tampered_agent_dll_is_refused(tmp_path, monkeypatch) -> None:
    from dolphin_desktop import _qt_agent

    fake = tmp_path / "dolphin_qt6_agent.dll"
    fake.write_bytes(b"MZ" + b"\x00" * 500)  # not the real binary
    monkeypatch.setattr(_qt_agent, "QT6_AGENT_DLL", fake)
    with pytest.raises(_qt_agent.AgentIntegrityError, match="does not match its recorded"):
        _qt_agent.agent_dll_for("6")


def test_agent_pipe_name_is_unguessable_and_per_attach() -> None:
    from dolphin_desktop._qt_inject import _agent_pipe_name

    a = _agent_pipe_name(4242)
    b = _agent_pipe_name(4242)
    assert a != b  # random token differs every attach
    assert a.startswith(r"\\.\pipe\dolphin_qt_4242_")
    token = a.rsplit("_", 1)[1]
    assert len(token) >= 24  # 16 random bytes as hex


# --------------------------------------------------------------------------- #
# KAN-471 — CDP screenshot decoding                                            #
# --------------------------------------------------------------------------- #


def _png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buf, "PNG")
    return buf.getvalue()


def _jpeg_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buf, "JPEG")
    return buf.getvalue()


def test_decode_screenshot_accepts_png_and_jpeg() -> None:
    from dolphin_desktop._cdp import _decode_screenshot

    assert _decode_screenshot(_png_bytes()).size == (2, 2)
    assert _decode_screenshot(_jpeg_bytes()).size == (2, 2)


@pytest.mark.parametrize(
    "payload",
    [
        b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 1 1\nshowpage\n",  # EPS
        b"GIF89a\x01\x00\x01\x00",  # GIF
        b"not an image at all",
        b"",
    ],
)
def test_decode_screenshot_refuses_non_png_jpeg(payload: bytes) -> None:
    from dolphin_desktop._cdp import _decode_screenshot
    from dolphin_desktop._exceptions import DolphinError

    with pytest.raises(DolphinError):
        _decode_screenshot(payload)


def test_decode_screenshot_enforces_a_size_cap(monkeypatch) -> None:
    import dolphin_desktop._cdp as cdp
    from dolphin_desktop._exceptions import DolphinError

    monkeypatch.setattr(cdp, "MAX_SCREENSHOT_BYTES", 8)
    with pytest.raises(DolphinError, match="exceeds"):
        cdp._decode_screenshot(_png_bytes())


# --------------------------------------------------------------------------- #
# KAN-472 — PID-reuse-safe termination                                         #
# --------------------------------------------------------------------------- #


def _fake_win32(monkeypatch) -> list[tuple]:
    calls: list[tuple] = []
    monkeypatch.setitem(
        sys.modules,
        "win32api",
        types.SimpleNamespace(
            OpenProcess=lambda *a: calls.append(("open", *a)) or "H",
            TerminateProcess=lambda *a: calls.append(("terminate", *a)),
            CloseHandle=lambda *a: calls.append(("close", *a)),
        ),
    )
    monkeypatch.setitem(sys.modules, "win32con", types.SimpleNamespace(PROCESS_TERMINATE=1))
    return calls


def test_terminate_skips_a_reused_pid(monkeypatch) -> None:
    from dolphin_desktop import _application

    calls = _fake_win32(monkeypatch)
    monkeypatch.setattr(_application, "_process_identities", {4242: 111})
    # The PID now reports a different creation time — it was reused.
    monkeypatch.setattr(_application, "_process_creation_time", lambda pid: 999)
    killed = _application.terminate_tracked_pid(4242, log=Mock())
    assert killed is False
    assert not any(c[0] == "terminate" for c in calls)
    assert 4242 not in _application._process_identities  # identity forgotten


def test_terminate_kills_a_matching_identity(monkeypatch) -> None:
    from dolphin_desktop import _application

    calls = _fake_win32(monkeypatch)
    monkeypatch.setattr(_application, "_process_identities", {4242: 111})
    monkeypatch.setattr(_application, "_process_creation_time", lambda pid: 111)
    killed = _application.terminate_tracked_pid(4242, log=Mock())
    assert killed is True
    assert ("terminate", "H", 1) in calls


def test_terminate_kills_when_identity_was_never_recorded(monkeypatch) -> None:
    from dolphin_desktop import _application

    calls = _fake_win32(monkeypatch)
    monkeypatch.setattr(_application, "_process_identities", {})
    monkeypatch.setattr(_application, "_process_creation_time", lambda pid: None)
    killed = _application.terminate_tracked_pid(9001, log=Mock())
    assert killed is True
    assert ("terminate", "H", 1) in calls


# --------------------------------------------------------------------------- #
# KAN-473 — trusted native library loading                                     #
# --------------------------------------------------------------------------- #


def test_load_trusted_dll_refuses_a_relative_path() -> None:
    from dolphin_desktop._native import NativeLibraryError, load_trusted_dll

    with pytest.raises(NativeLibraryError, match="absolute path"):
        load_trusted_dll("evil.dll", what="test DLL")


def test_load_trusted_dll_refuses_a_missing_file(tmp_path) -> None:
    from dolphin_desktop._native import NativeLibraryError, load_trusted_dll

    with pytest.raises(NativeLibraryError, match="does not exist"):
        load_trusted_dll(str(tmp_path / "nope.dll"), what="test DLL")


def test_existing_candidates_only_returns_files_that_exist(tmp_path) -> None:
    from dolphin_desktop._native import existing_candidates

    (tmp_path / "there.dll").write_bytes(b"MZ")
    found = existing_candidates([str(tmp_path)], ["there.dll", "missing.dll"])
    assert found == [str(tmp_path / "there.dll")]


# --------------------------------------------------------------------------- #
# KAN-475 — CDP endpoint ownership                                             #
# --------------------------------------------------------------------------- #


def test_port_owned_by_requires_every_owner_to_be_allowed() -> None:
    import dolphin_desktop._netinfo as netinfo

    original = netinfo.loopback_listener_pids
    try:
        netinfo.loopback_listener_pids = lambda port: {100}  # type: ignore[assignment]
        assert netinfo.port_owned_by(9222, {100, 200}) == (True, {100})
        netinfo.loopback_listener_pids = lambda port: {100, 300}  # type: ignore[assignment]
        owned, owners = netinfo.port_owned_by(9222, {100, 200})
        assert owned is False and owners == {100, 300}
        netinfo.loopback_listener_pids = lambda port: set()  # type: ignore[assignment]
        assert netinfo.port_owned_by(9222, {100}) == (False, set())
    finally:
        netinfo.loopback_listener_pids = original  # type: ignore[assignment]


def test_verify_cdp_port_owner_rejects_a_foreign_pid(monkeypatch) -> None:
    from dolphin_desktop import _desktop

    desktop = _desktop.Desktop(hidden=False)
    app = SimpleNamespace(process_id=1000, kill=Mock())
    monkeypatch.setattr("dolphin_desktop._netinfo.loopback_listener_pids", lambda port: {5555})
    monkeypatch.setattr(
        "dolphin_desktop._application._enumerate_descendant_pids", lambda pid: set()
    )
    with pytest.raises(RuntimeError, match="not the application dolphin launched"):
        desktop._verify_cdp_port_owner(app, 9222, "Electron", lambda p: f"port {p}")
    app.kill.assert_called_once()


def test_verify_cdp_port_owner_accepts_a_descendant(monkeypatch) -> None:
    from dolphin_desktop import _desktop

    desktop = _desktop.Desktop(hidden=False)
    app = SimpleNamespace(process_id=1000, kill=Mock())
    monkeypatch.setattr("dolphin_desktop._netinfo.loopback_listener_pids", lambda port: {1234})
    monkeypatch.setattr(
        "dolphin_desktop._application._enumerate_descendant_pids", lambda pid: {1234, 5678}
    )
    # No exception: the listener is a verified descendant of the launched pid.
    desktop._verify_cdp_port_owner(app, 9222, "Electron", lambda p: f"port {p}")
    app.kill.assert_not_called()


def test_verify_cdp_port_owner_is_lenient_when_lookup_unavailable(monkeypatch) -> None:
    from dolphin_desktop import _desktop

    desktop = _desktop.Desktop(hidden=False)
    app = SimpleNamespace(process_id=1000, kill=Mock())
    # Empty owner set == "cannot determine" — keep the HTTP-only contract.
    monkeypatch.setattr("dolphin_desktop._netinfo.loopback_listener_pids", lambda port: set())
    desktop._verify_cdp_port_owner(app, 9222, "Electron", lambda p: f"port {p}")
    app.kill.assert_not_called()


# --------------------------------------------------------------------------- #
# KAN-476 — launch_qt does not mutate the parent environment                   #
# --------------------------------------------------------------------------- #


def test_concurrent_launch_qt_never_touches_parent_environ(monkeypatch) -> None:
    from dolphin_desktop import _desktop, start_thread

    monkeypatch.delenv("QT_ACCESSIBILITY", raising=False)
    desktop = _desktop.Desktop(hidden=False)
    seen_envs: list[dict] = []
    parent_leaks: list[str | None] = []
    lock = __import__("threading").Lock()

    def fake_launch(cmd, *, env=None, **kwargs):
        # Record what the child would get and whether the parent leaked.
        with lock:
            seen_envs.append(dict(env or {}))
            parent_leaks.append(os.environ.get("QT_ACCESSIBILITY"))
        return SimpleNamespace(cmd=cmd)

    monkeypatch.setattr(desktop, "launch", fake_launch)

    def run(i: int) -> None:
        desktop.launch_qt(f"app{i}.exe", qt_env={"QT_LOGGING_RULES": f"r{i}"})

    threads = [start_thread(run, args=(i,)) for i in range(12)]
    for t in threads:
        t.join(timeout=5)

    assert len(seen_envs) == 12
    # Every child got QT_ACCESSIBILITY=1 in its private block...
    assert all(env.get("QT_ACCESSIBILITY") == "1" for env in seen_envs)
    # ...and the parent process environment was never mutated by any launch.
    assert all(leak is None for leak in parent_leaks)
    assert os.environ.get("QT_ACCESSIBILITY") is None


# --------------------------------------------------------------------------- #
# KAN-477 — http_ok URL scheme restriction                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url",
    [
        "file:///c:/windows/win.ini",
        "ftp://example.com/x",
        "gopher://example.com",
        "http://",  # no host
        "://nohost",
        "data:text/plain,hi",
    ],
)
def test_http_ok_refuses_non_http_or_hostless_urls(url: str, monkeypatch) -> None:
    from dolphin_desktop import _helpers

    def _explode(*a, **k):
        raise AssertionError(f"urlopen must not be called for {url!r}")

    monkeypatch.setattr("urllib.request.urlopen", _explode)
    assert _helpers.http_ok(url) is False


def test_http_ok_still_probes_http_and_https(monkeypatch) -> None:
    from dolphin_desktop import _helpers

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

    calls: list[str] = []

    def _urlopen(url, timeout=None):
        calls.append(url)
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    assert _helpers.http_ok("http://127.0.0.1:9222/json/version") is True
    assert _helpers.http_ok("https://127.0.0.1:9222/json/version") is True
    assert len(calls) == 2
