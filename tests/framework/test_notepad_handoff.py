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
    monkeypatch.setattr(application, "_process_creation_time", lambda pid: 200)
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
        "running",
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
    elif reason in {"running", "unknown_state"}:
        monkeypatch.setattr(
            application,
            "_process_state",
            lambda pid: "running" if reason == "running" else "unknown",
        )
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
