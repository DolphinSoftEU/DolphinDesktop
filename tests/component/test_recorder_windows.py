"""Real native focus and low-level hook regressions for KAN-576 / KAN-759."""

import json
from unittest.mock import Mock

import pytest
from tests.component import _recorder_window
from tests.component._recorder_window import (
    check_real_hook_lifecycle,
    record_generate_replay,
    record_native_transitions,
    record_process_isolation,
)

from dolphin_desktop._recorder import _PASSWORD_PLACEHOLDER

pytestmark = [pytest.mark.windows_component, pytest.mark.timeout(20)]


def test_worker_query_pumps_same_process_window_messages():
    # No foreground activation or desktop input is needed for WM_GETTEXT.
    gui = _recorder_window.win32gui
    root = gui.CreateWindowEx(
        0,
        "Static",
        "KAN-759 worker query",
        0,
        0,
        0,
        100,
        100,
        0,
        0,
        _recorder_window.win32api.GetModuleHandle(None),
        None,
    )
    try:
        owner = _recorder_window.win32api.GetCurrentThreadId()

        def query():
            assert _recorder_window.win32api.GetCurrentThreadId() != owner
            return gui.GetWindowText(root)

        assert _recorder_window._query_on_worker(query) == "KAN-759 worker query"
    finally:
        gui.DestroyWindow(root)


def test_worker_query_timeout_does_not_wait_for_worker_shutdown():
    release = _recorder_window.threading.Event()
    finished = _recorder_window.threading.Event()

    def query():
        try:
            assert release.wait(5), "Test did not release the blocked worker"
        finally:
            finished.set()

    try:
        with pytest.raises(TimeoutError, match="native context worker"):
            _recorder_window._query_on_worker(query, timeout=0.05)
        # The caller must receive the timeout before the worker is unblocked.
        assert not finished.is_set()
    finally:
        release.set()
        assert finished.wait(2), "Controlled worker did not exit"


def test_worker_query_propagates_failure():
    failure = RuntimeError("controlled worker failure")
    with pytest.raises(RuntimeError) as caught:
        _recorder_window._query_on_worker(Mock(side_effect=failure))
    assert caught.value is failure


@pytest.mark.parametrize("text", ["", "tttt", "zażółć"])
def test_native_edit_diagnostic_reads_actual_control_text(text):
    gui = _recorder_window.win32gui
    edit = gui.CreateWindowEx(
        0,
        "Edit",
        text,
        0,
        0,
        0,
        100,
        100,
        0,
        0,
        _recorder_window.win32api.GetModuleHandle(None),
        None,
    )
    try:
        assert _recorder_window._native_edit_text(edit) == text
    finally:
        gui.DestroyWindow(edit)


@pytest.mark.parametrize("status", [0, 1])
def test_native_read_status_distinguishes_failure_from_empty_text(monkeypatch, tmp_path, status):
    field = Mock()
    field.text.return_value = ""
    field._resolve.return_value.handle = 789
    rec = Mock(_processed=5, _hook_error=None)
    monkeypatch.setattr(
        _recorder_window.win32gui, "SendMessageTimeout", Mock(return_value=(status, 0))
    )
    monkeypatch.setattr(
        _recorder_window._recorder,
        "_capture_context",
        lambda: _recorder_window._recorder._InputContext(123, 456, 789, password=False),
    )
    monkeypatch.setattr(_recorder_window.win32gui, "GetForegroundWindow", lambda: 123)
    output = tmp_path / "recorded.py"

    def read():
        _recorder_window._wait_for_edit_text(
            field, 123, 789, "", "cleared before replay", rec, output, timeout=0
        )

    if status:
        read()
    else:
        with pytest.raises(OSError, match=r"WM_GETTEXT.*zero status"):
            read()
    evidence = json.loads(output.with_suffix(".diagnostics.json").read_text(encoding="utf-8"))[-1]
    if status:
        assert evidence["native_text"] == ""
        assert "errors" not in evidence
    else:
        assert "native_text" not in evidence
        assert evidence["errors"]["native_text"]["type"] == "OSError"
        assert "zero status" in evidence["errors"]["native_text"]["message"]


@pytest.mark.parametrize("uia_text,native_text", [("tttt", ""), ("", "tttt"), ("TTTT", "TTTT")])
def test_replay_diagnostic_identifies_stage_and_text_disagreement(
    monkeypatch, tmp_path, uia_text, native_text
):
    field = Mock()
    field.text.return_value = uia_text
    field._resolve.return_value.handle = 789
    rec = Mock(_processed=5, _hook_error=None)
    monkeypatch.setattr(_recorder_window, "_native_edit_text", lambda hwnd: native_text)
    monkeypatch.setattr(
        _recorder_window._recorder,
        "_capture_context",
        lambda: _recorder_window._recorder._InputContext(123, 456, 789, password=False),
    )
    monkeypatch.setattr(_recorder_window.win32gui, "GetForegroundWindow", lambda: 123)
    output = tmp_path / "recorded.py"
    with pytest.raises(AssertionError, match="generated script replay"):
        _recorder_window._wait_for_edit_text(
            field, 123, 789, "tttt", "generated script replay", rec, output, timeout=0
        )
    evidence = json.loads(output.with_suffix(".diagnostics.json").read_text(encoding="utf-8"))
    assert evidence[-1]["uia_text"] == uia_text
    assert evidence[-1]["native_text"] == native_text
    assert evidence[-1]["stage"] == "generated script replay"


@pytest.mark.parametrize("failed_probe", ["uia_text", "native_text", "resolved_hwnd"])
def test_replay_diagnostic_saves_partial_data_before_reraising(monkeypatch, tmp_path, failed_probe):
    field = Mock()
    field.text.return_value = "tttt"
    field._resolve.return_value.handle = 789
    rec = Mock(_processed=5, _hook_error=None)
    native = Mock(return_value="tttt")
    failure = OSError("controlled diagnostic read failure")
    probes = {"uia_text": field.text, "native_text": native, "resolved_hwnd": field._resolve}
    probes[failed_probe].side_effect = failure
    monkeypatch.setattr(_recorder_window, "_native_edit_text", native)
    monkeypatch.setattr(
        _recorder_window._recorder,
        "_capture_context",
        lambda: _recorder_window._recorder._InputContext(123, 456, 789, password=False),
    )
    monkeypatch.setattr(_recorder_window.win32gui, "GetForegroundWindow", lambda: 123)
    output = tmp_path / "recorded.py"
    report = output.with_suffix(".diagnostics.json")
    report.write_text('[{"stage": "recorded input"}]', encoding="utf-8")

    with pytest.raises(OSError) as caught:
        _recorder_window._wait_for_edit_text(
            field, 123, 789, "tttt", "generated script replay", rec, output, timeout=0
        )

    assert caught.value is failure
    history = json.loads(report.read_text(encoding="utf-8"))
    assert history[0] == {"stage": "recorded input"}
    evidence = history[1]
    assert evidence["stage"] == "generated script replay"
    assert evidence["errors"][failed_probe] == {
        "type": "OSError",
        "message": "controlled diagnostic read failure",
    }
    assert failed_probe not in evidence
    assert evidence["foreground_hwnd"] == 123
    assert evidence["focus_hwnd"] == 789
    for name, expected in (("uia_text", "tttt"), ("native_text", "tttt"), ("resolved_hwnd", 789)):
        if name != failed_probe:
            assert evidence[name] == expected


def test_real_hook_installation_and_cleanup():
    check_real_hook_lifecycle()


def test_real_hook_delayed_password_text_transitions():
    actions, code = record_native_transitions()
    assert [(action.kind, action.data) for action in actions] == [
        ("type_text", _PASSWORD_PLACEHOLDER),
        ("type_text", "tttt"),
        ("type_text", _PASSWORD_PLACEHOLDER),
        ("type_text", _PASSWORD_PLACEHOLDER),
    ]
    assert "tttt" in code
    for secret in ("qqqq", "ssss", "kkkk"):
        assert secret not in code


def test_real_record_generate_replay_targets_native_edit(tmp_path):
    record_generate_replay(tmp_path / "recorded.py")


def test_real_hook_delayed_events_stay_in_target_process(tmp_path):
    actions, code = record_process_isolation(tmp_path)
    assert [action.data for action in actions if action.kind == "type_text"] == ["tttt", "ssss"]
    assert actions
    assert all(action.window_title == "KAN-576 owned" for action in actions)
    assert "tttt" in code
    assert "ssss" in code
    for foreign_text in ("qqqq", "zzzz", "KAN-576 foreign"):
        assert foreign_text not in code


def test_replay_cleanup_closes_aut_when_recorder_stop_fails(monkeypatch, tmp_path):
    import dolphin_desktop
    from dolphin_desktop import _application

    output = tmp_path / "recorded.py"
    output.with_suffix(".ready.json").write_text(json.dumps({"hwnd": 123, "pid": 456}))
    process = Mock(pid=456)
    process.poll.return_value = None
    handle = Mock()
    recorder = Mock()
    failure = OSError("controlled recorder shutdown failure")
    recorder.stop.side_effect = failure
    recorder._hook_thread.is_alive.return_value = False
    app = Mock()
    # Fail before input is sent, after both the recorder and app are acquired.
    app.window.side_effect = RuntimeError("controlled setup failure")
    monkeypatch.setattr(_recorder_window.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(_application, "_enumerate_descendant_pids", lambda pid: set())
    monkeypatch.setattr(
        _recorder_window.win32process, "GetWindowThreadProcessId", lambda hwnd: (1, 456)
    )
    monkeypatch.setattr(_recorder_window.win32api, "OpenProcess", Mock(return_value=handle))
    monkeypatch.setattr(_recorder_window._recorder, "Recorder", Mock(return_value=recorder))
    monkeypatch.setattr(_recorder_window.win32gui, "GetDlgItem", Mock(return_value=789))
    monkeypatch.setattr(_recorder_window, "_activate_owned_window", Mock())
    monkeypatch.setattr(
        dolphin_desktop, "Desktop", Mock(return_value=Mock(connect=Mock(return_value=app)))
    )
    monkeypatch.setattr(
        _recorder_window.win32process,
        "GetExitCodeProcess",
        lambda value: _recorder_window.win32con.STILL_ACTIVE,
    )
    terminate = Mock()
    monkeypatch.setattr(_recorder_window.win32api, "TerminateProcess", terminate)

    with pytest.raises(OSError) as caught:
        record_generate_replay(output)

    assert caught.value is failure
    recorder.stop.assert_called_once_with()
    recorder._hook_thread.join.assert_called_once_with(3)
    app.detach.assert_called_once_with()
    terminate.assert_called_once_with(handle, 0)
    handle.Close.assert_called_once_with()
    process.terminate.assert_called_once_with()
    process.communicate.assert_called_once_with(timeout=5)
