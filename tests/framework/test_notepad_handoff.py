"""Windows 11 Notepad activation and ownership regression tests (KAN-660)."""

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dolphin_desktop import _application as application
from dolphin_desktop import _desktop as desktop_module

SYSTEM_NOTEPAD = r"C:\Windows\System32\notepad.exe"
MSIX_NOTEPAD = (
    r"C:\Program Files\WindowsApps\Microsoft.WindowsNotepad_11.2607.14.0_x64__8wekyb3d8bbwe"
    r"\Notepad\Notepad.exe"
)


@pytest.fixture
def handoff(monkeypatch):
    for name in ("_live_pids", "_session_pids", "_unanchored_pids", "_attached_pids"):
        monkeypatch.setattr(application, name, set())
    monkeypatch.setattr(application, "_owned_process_handles", {})
    monkeypatch.setattr(application, "_process_identities", {})
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    monkeypatch.setattr(application, "_process_creation_time", lambda pid: 100 if pid == 1 else 200)
    monkeypatch.setattr(application, "_process_parent_map", lambda: {99: [1], 1: [2, 3]})
    monkeypatch.setattr(desktop_module, "_snapshot_process_ids", lambda: {99})
    monkeypatch.setattr(
        application,
        "_process_creation_time_from_handle",
        lambda handle: 100 if handle == 10 else 200,
    )
    monkeypatch.setattr(application, "_process_state", lambda pid: "stopped")
    monkeypatch.setattr(application, "_process_image_path", lambda pid: MSIX_NOTEPAD)
    monkeypatch.setattr(
        application, "_process_package_family", lambda pid: "Microsoft.WindowsNotepad_8wekyb3d8bbwe"
    )
    monkeypatch.setattr(application, "_open_owned_process_handle", Mock(return_value=20))
    monkeypatch.setattr(application, "_close_owned_process_handle", Mock())
    monkeypatch.setattr(application, "_visible_window_handles", lambda pid: [])
    monkeypatch.setattr(application, "_last_active_popup_handle", lambda pid: None)
    raw = Mock(process=1)
    raw.windows.return_value = []
    target = Mock(process=2)
    wrapper = Mock(handle=42)
    wrapper.process_id.return_value = 2
    target.windows.return_value = [wrapper]
    desktop = Mock()
    desktop.windows.return_value = [wrapper]
    desktop.window.return_value.wrapper_object.return_value = wrapper
    monkeypatch.setattr(application, "_PwDesktop", Mock(return_value=desktop))
    monkeypatch.setattr(application, "_PyWinApp", Mock(return_value=target))
    app = application.Application(raw, "uia", image_path=SYSTEM_NOTEPAD, owned_process_handle=10)
    app._launch_snapshot = {99}
    return SimpleNamespace(app=app, raw=raw, target=target, desktop=desktop, wrapper=wrapper)


@pytest.mark.parametrize("source", [SYSTEM_NOTEPAD, MSIX_NOTEPAD])
def test_public_launch_waits_for_msix_window_and_transfers_cleanup(handoff, monkeypatch, source):
    desktop = desktop_module.Desktop(hidden=False)
    monkeypatch.setattr(
        desktop, "_launch_with_environment", Mock(return_value=(handoff.raw, source, 10))
    )
    handoff.desktop.windows.side_effect = [[], [handoff.wrapper]]
    monkeypatch.setattr(application.time, "sleep", Mock())
    app = desktop.launch(SYSTEM_NOTEPAD, startup_delay=0, timeout=1)
    assert app.process_id == 2
    assert len(app.windows()) == 1
    assert app.top_window()._spec is handoff.target.top_window.return_value
    assert application._live_pids == {2}
    assert application._session_pids == {2}
    assert application._owned_process_handles == {2: 20}
    application._close_owned_process_handle.assert_called_once_with(10)


@pytest.mark.parametrize("accessor", ["windows", "top_window", "kill", "close"])
def test_accessors_and_cleanup_resolve_handoff_without_title_search(handoff, monkeypatch, accessor):
    terminate = Mock()
    monkeypatch.setattr(handoff.app, "_terminate_owned_process", terminate)
    # An exited launcher's pywinauto wrapper need not allow enumeration.
    handoff.raw.windows.side_effect = RuntimeError("launcher exited")
    getattr(handoff.app, accessor)()
    assert handoff.app.process_id == 2
    assert handoff.app._owned_process_handle == 20
    if accessor in {"kill", "close"}:
        terminate.assert_called_once_with(soft=accessor == "close")
    else:
        terminate.assert_not_called()


@pytest.mark.parametrize(
    "reason",
    [
        "existing",
        "unknown_time",
        "unknown_start",
        "reused_launcher",
        "unknown_state",
        "wrong_package",
        "unrelated_image",
        "attached",
        "ambiguous",
        "no_handle",
        "reused",
    ],
)
def test_handoff_fails_closed_and_keeps_original_ownership(handoff, monkeypatch, reason):
    if reason in {"existing", "unknown_time"}:
        monkeypatch.setattr(
            application, "_process_creation_time", lambda pid: 99 if reason == "existing" else None
        )
    elif reason == "unknown_start":
        handoff.app._launch_creation_time = None
    elif reason == "reused_launcher":
        monkeypatch.setattr(
            application, "_process_creation_time", lambda pid: 999 if pid == 1 else 200
        )
    elif reason == "unknown_state":
        monkeypatch.setattr(
            application, "_process_creation_time", lambda pid: None if pid == 1 else 200
        )
        monkeypatch.setattr(application, "_process_state", lambda pid: "unknown")
    elif reason == "wrong_package":
        monkeypatch.setattr(application, "_process_package_family", lambda pid: "OtherPackage")
    elif reason == "unrelated_image":
        monkeypatch.setattr(application, "_process_image_path", lambda pid: r"C:\Other\other.exe")
    elif reason == "attached":
        handoff.app._owns_process = False
    elif reason == "ambiguous":
        other = Mock(handle=43)
        other.process_id.return_value = 3
        handoff.desktop.windows.return_value.append(other)
    elif reason == "no_handle":
        monkeypatch.setattr(application, "_open_owned_process_handle", lambda pid: None)
    elif reason == "reused":
        monkeypatch.setattr(application, "_process_creation_time_from_handle", lambda handle: 999)
    handoff.app._resolve_launch_handoff()
    assert handoff.app.process_id == 1
    assert application._owned_process_handles == {1: 10}
    assert application._live_pids == {1}


def test_msix_source_does_not_adopt_preexisting_same_image(handoff, monkeypatch):
    handoff.app._image_path = MSIX_NOTEPAD
    monkeypatch.setattr(application, "_process_creation_time", lambda pid: 99)
    assert not handoff.app._is_hand_off_pid(2)


@pytest.mark.parametrize("failure", ["no_handle", "reused_handle", "unknown_identity", "connect"])
def test_public_window_rejects_unverified_ownership(handoff, monkeypatch, failure):
    handoff.raw.window.side_effect = RuntimeError("no window in launcher")
    if failure == "no_handle":
        monkeypatch.setattr(application, "_open_owned_process_handle", lambda pid: None)
    elif failure in {"reused_handle", "unknown_identity"}:
        monkeypatch.setattr(
            application,
            "_process_creation_time_from_handle",
            lambda handle: 999 if failure == "reused_handle" else None,
        )
    else:
        handoff.target.connect.side_effect = RuntimeError("access denied")

    with pytest.raises(application.WindowNotFoundError, match="Could not adopt window"):
        handoff.app.window(title_re=".*Notepad.*", timeout=0)

    assert handoff.app.process_id == 1
    assert handoff.app._owned_process_handle == 10
    assert application._owned_process_handles == {1: 10}
    assert application._live_pids == {1}
    assert application._session_pids == {1}


@pytest.mark.parametrize("source", [SYSTEM_NOTEPAD, MSIX_NOTEPAD])
def test_independent_notepad_started_after_launcher_is_not_owned(handoff, monkeypatch, source):
    handoff.app._image_path = source
    # The new process has the correct image, package and creation time, but
    # comes from a separate launcher. A before/after delta alone is not proof.
    monkeypatch.setattr(application, "_process_parent_map", lambda: {99: [1, 2]})
    handoff.raw.window.side_effect = RuntimeError("no window in launcher")
    assert not handoff.app._is_hand_off_pid(2)
    with pytest.raises(application.WindowNotFoundError, match="neither this application"):
        handoff.app.window(title_re=".*Notepad.*", timeout=0)
    terminate = Mock()
    monkeypatch.setattr(handoff.app, "_terminate_owned_process", terminate)
    handoff.app.kill()
    assert handoff.app.process_id == 1
    terminate.assert_called_once_with(soft=False)
    handoff.target.kill.assert_not_called()
    application._open_owned_process_handle.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    [
        "missing_baseline",
        "baseline_pid",
        "cycle",
        "older_parent",
        "younger_parent",
        "unknown_parent",
        "missing_parent",
    ],
)
def test_unproven_ancestry_never_grants_notepad_ownership(handoff, monkeypatch, failure):
    if failure == "missing_baseline":
        handoff.app._launch_snapshot = None
    elif failure == "baseline_pid":
        handoff.app._launch_snapshot = {2}
    elif failure == "cycle":
        monkeypatch.setattr(application, "_process_parent_map", lambda: {3: [2], 2: [3]})
    elif failure in {"older_parent", "younger_parent", "unknown_parent"}:
        monkeypatch.setattr(application, "_process_parent_map", lambda: {1: [3], 3: [2]})
        parent_time = {"older_parent": 99, "younger_parent": 999, "unknown_parent": None}[failure]
        monkeypatch.setattr(
            application,
            "_process_creation_time",
            lambda pid: parent_time if pid == 3 else (100 if pid == 1 else 200),
        )
    else:
        monkeypatch.setattr(application, "_process_parent_map", dict)
    handoff.app._resolve_launch_handoff()
    assert handoff.app.process_id == 1
    assert application._owned_process_handles == {1: 10}


def test_new_notepad_grandchild_can_be_owned(handoff, monkeypatch):
    monkeypatch.setattr(application, "_process_parent_map", lambda: {1: [3], 3: [2]})
    monkeypatch.setattr(
        application, "_process_creation_time", lambda pid: {1: 100, 3: 150, 2: 200}[pid]
    )
    assert len(handoff.app.windows()) == 1
    assert handoff.app.process_id == 2
    assert application._owned_process_handles == {2: 20}


@pytest.mark.parametrize("state", ["running", "stopped"])
def test_existing_notepad_returns_without_spending_handoff_timeout(handoff, monkeypatch, state):
    handoff.app._launch_snapshot = {2}
    monkeypatch.setattr(application, "_process_state", lambda pid: state)
    sleep = Mock()
    monkeypatch.setattr(application.time, "sleep", sleep)
    handoff.app._resolve_launch_handoff(timeout=15)
    sleep.assert_not_called()
    handoff.desktop.windows.assert_called_once_with()
    assert handoff.app.process_id == 1
    assert application._owned_process_handles == {1: 10}


def test_verified_descendant_takes_priority_over_existing_notepad(handoff):
    old = Mock(handle=43)
    old.process_id.return_value = 3
    handoff.app._launch_snapshot = {3}
    handoff.desktop.windows.return_value = [old, handoff.wrapper]
    handoff.app._resolve_launch_handoff(timeout=15)
    assert handoff.app.process_id == 2
    assert application._owned_process_handles == {2: 20}


@pytest.mark.parametrize("parents,expected", [({}, None), ({99: [1], 1: [2]}, {1, 2})])
def test_process_baseline_distinguishes_snapshot_failure(monkeypatch, parents, expected):
    monkeypatch.setattr(application, "_process_parent_map", lambda: parents)
    assert application._snapshot_process_ids() == expected


@pytest.mark.parametrize("timeout", [0, 0.25, 2])
def test_public_launch_has_separate_handoff_timeout(handoff, monkeypatch, timeout):
    desktop = desktop_module.Desktop(hidden=False)
    monkeypatch.setattr(
        desktop, "_launch_with_environment", Mock(return_value=(handoff.raw, SYSTEM_NOTEPAD, 10))
    )
    resolver = Mock()
    monkeypatch.setattr(application.Application, "_resolve_launch_handoff", resolver)
    desktop.launch(SYSTEM_NOTEPAD, timeout=15, handoff_timeout=timeout, startup_delay=0)
    resolver.assert_called_once_with(timeout=timeout)


@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf")])
def test_public_launch_rejects_invalid_handoff_timeout(timeout):
    with pytest.raises(ValueError, match="handoff_timeout must be finite and non-negative"):
        desktop_module.Desktop(hidden=False).launch(SYSTEM_NOTEPAD, handoff_timeout=timeout)


def test_classic_notepad_and_resolved_msix_do_not_scan_desktop_again(handoff):
    handoff.raw.windows.return_value = [handoff.wrapper]
    handoff.app._resolve_launch_handoff()
    handoff.desktop.windows.assert_not_called()
    handoff.raw.windows.return_value = []
    handoff.app._resolve_launch_handoff()
    handoff.desktop.windows.assert_not_called()


@pytest.mark.parametrize("result", [0, 15700, 5])
def test_package_identity_query_closes_handle_on_success_and_failure(monkeypatch, result):
    family = "Microsoft.WindowsNotepad_8wekyb3d8bbwe"

    def query(handle, size, buffer):
        buffer.value = family
        return result

    kernel = SimpleNamespace(
        OpenProcess=Mock(return_value=42),
        GetPackageFamilyName=Mock(side_effect=query),
        CloseHandle=Mock(),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    assert application._process_package_family(2) == (family if result == 0 else None)
    kernel.CloseHandle.assert_called_once_with(42)


@pytest.mark.parametrize("failure", ["open_denied", "open_error", "query_error", "close_error"])
def test_package_identity_query_fails_closed_on_api_errors(monkeypatch, failure):
    kernel = SimpleNamespace(
        OpenProcess=Mock(return_value=42),
        GetPackageFamilyName=Mock(return_value=0),
        CloseHandle=Mock(),
    )
    if failure == "open_denied":
        kernel.OpenProcess.return_value = 0
    elif failure == "open_error":
        kernel.OpenProcess.side_effect = OSError("process unavailable")
    elif failure == "query_error":
        kernel.GetPackageFamilyName.side_effect = OSError("query failed")
    else:
        kernel.CloseHandle.side_effect = OSError("close failed")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)

    assert application._process_package_family(2) is None
    if failure in {"open_denied", "open_error"}:
        kernel.GetPackageFamilyName.assert_not_called()
        kernel.CloseHandle.assert_not_called()
    else:
        kernel.CloseHandle.assert_called_once_with(42)


def test_desktop_enumeration_failure_preserves_launcher_ownership(handoff):
    handoff.desktop.windows.side_effect = RuntimeError("desktop unavailable")

    handoff.app._resolve_launch_handoff(timeout=0)

    assert handoff.app.process_id == 1
    assert handoff.app._owned_process_handle == 10
    assert application._owned_process_handles == {1: 10}
    assert application._live_pids == {1}
    assert application._session_pids == {1}
    application._close_owned_process_handle.assert_not_called()


def test_transient_desktop_enumeration_failure_retries_within_timeout(handoff, monkeypatch):
    handoff.desktop.windows.side_effect = [RuntimeError("desktop unavailable"), [handoff.wrapper]]
    monkeypatch.setattr(application.time, "sleep", Mock())

    handoff.app._resolve_launch_handoff(timeout=1)

    assert handoff.app.process_id == 2
    assert application._owned_process_handles == {2: 20}
    assert application._live_pids == {2}
    assert application._session_pids == {2}


def test_duplicate_windows_and_disappearing_window_do_not_prevent_handoff(handoff):
    gone = Mock()
    gone.process_id.side_effect = RuntimeError("window disappeared")
    handoff.desktop.windows.return_value = [gone, handoff.wrapper, handoff.wrapper]
    handoff.app._resolve_launch_handoff()
    assert handoff.app.process_id == 2


@pytest.mark.parametrize(
    "image,expected",
    [
        (SYSTEM_NOTEPAD, True),
        (MSIX_NOTEPAD, True),
        (MSIX_NOTEPAD.lower(), True),
        (r"C:\Other\notepad.exe", False),
        (None, False),
        (MSIX_NOTEPAD.replace("8wekyb3d8bbwe", "otherpublisher"), False),
        (MSIX_NOTEPAD.replace("Notepad.exe", "other.exe"), False),
    ],
)
def test_only_windows_notepad_images_enable_discovery(image, expected):
    assert application._is_notepad_launcher(image) is expected
