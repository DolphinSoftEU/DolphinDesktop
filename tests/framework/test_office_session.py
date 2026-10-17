"""Exclusive Office ownership must never infer absence from a failed probe."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dolphin_desktop import OutlookApp, PowerPointApp
from dolphin_desktop import _office_session as sessions
from dolphin_desktop import _outlook as outlook
from dolphin_desktop import _powerpoint as ppt


def test_shared_start_does_not_claim_or_probe_processes(monkeypatch):
    client = MagicMock()
    probe = MagicMock(side_effect=AssertionError("shared session must not probe"))
    monkeypatch.setattr(sessions, "_office_pids", probe)
    com, session = sessions._start_office(
        client, "Office.Application", "OFFICE.EXE", exclusive=False
    )
    assert com is client.Dispatch.return_value
    assert session is None


def test_snapshot_matches_executable_name_without_hiding_probe_errors(monkeypatch):
    monkeypatch.setattr(
        sessions, "_process_entries", lambda: [("powerpnt.exe", 42), ("other.exe", 43)]
    )
    assert sessions._office_pids("POWERPNT.EXE") == [42]
    monkeypatch.setattr(
        sessions, "_process_entries", MagicMock(side_effect=OSError("snapshot failed"))
    )
    with pytest.raises(OSError, match="snapshot failed"):
        sessions._office_pids("POWERPNT.EXE")


@pytest.mark.parametrize("snapshot", [[42], OSError("tasklist failed")])
def test_exclusive_start_refuses_existing_or_unknown_processes(monkeypatch, snapshot):
    probe = MagicMock()
    if isinstance(snapshot, Exception):
        probe.side_effect = snapshot
    else:
        probe.return_value = snapshot
    monkeypatch.setattr(sessions, "_office_pids", probe)
    client = MagicMock()
    with pytest.raises((RuntimeError, OSError)):
        sessions._start_office(client, "Office.Application", "OFFICE.EXE", exclusive=True)
    client.Dispatch.assert_not_called()


def test_exclusive_start_only_claims_unique_new_process(monkeypatch):
    monkeypatch.setattr(sessions, "_office_pids", MagicMock(side_effect=[[], [42]]))
    anchor = MagicMock()
    monkeypatch.setattr(sessions, "_OfficeSession", anchor)
    client = MagicMock()
    com, session = sessions._start_office(
        client, "Office.Application", "OFFICE.EXE", exclusive=True
    )
    assert com is client.Dispatch.return_value
    assert session is anchor.return_value
    assert anchor.call_args.args[:2] == (42, "OFFICE.EXE")


@pytest.mark.parametrize("after", [[], [42, 43]])
def test_ambiguous_activation_never_quits_a_guessed_process(monkeypatch, after):
    monkeypatch.setattr(sessions, "_office_pids", MagicMock(side_effect=[[], after]))
    client = MagicMock()
    with pytest.raises(RuntimeError, match="Cannot establish"):
        sessions._start_office(client, "Office.Application", "OFFICE.EXE", exclusive=True)
    client.Dispatch.return_value.Quit.assert_not_called()


@pytest.mark.parametrize("creation,path", [(None, "x"), (99, "x"), (101, None), (101, "other.exe")])
def test_identity_failure_closes_anchor_without_claiming_process(monkeypatch, creation, path):
    handle = MagicMock()
    api = SimpleNamespace(OpenProcess=MagicMock(return_value=handle))
    monkeypatch.setitem(sys.modules, "win32api", api)
    monkeypatch.setattr(sessions, "_process_creation_time_from_handle", lambda _: creation)
    monkeypatch.setattr(sessions, "_process_image_path", lambda _: path)
    with pytest.raises(RuntimeError):
        sessions._OfficeSession(42, "OFFICE.EXE", 100)
    handle.Close.assert_called_once_with()


@pytest.fixture
def anchored_session(monkeypatch):
    handle = MagicMock()
    monkeypatch.setitem(sys.modules, "win32api", SimpleNamespace(OpenProcess=lambda *_: handle))
    monkeypatch.setattr(sessions, "_process_creation_time_from_handle", lambda _: 101)
    monkeypatch.setattr(sessions, "_process_image_path", lambda _: r"C:\Office\OFFICE.EXE")
    session = sessions._OfficeSession(42, "OFFICE.EXE", 100)
    wait = MagicMock(side_effect=[258, 0])
    monkeypatch.setitem(sys.modules, "win32event", SimpleNamespace(WaitForSingleObject=wait))
    monkeypatch.setitem(sys.modules, "pythoncom", SimpleNamespace(PumpWaitingMessages=MagicMock()))
    return session, handle, wait


def test_quit_waits_for_anchored_process_and_is_idempotent(anchored_session):
    session, handle, wait = anchored_session
    com = MagicMock()
    session.quit(com)
    session.quit(com)
    com.Quit.assert_called_once_with()
    assert wait.call_args_list[1].args == (handle, 50)
    assert session.closed
    handle.Close.assert_called_once_with()


def test_already_exited_original_does_not_call_com_quit_on_possible_replacement(anchored_session):
    session, handle, wait = anchored_session
    wait.side_effect = [0]
    com = MagicMock()
    session.quit(com)
    com.Quit.assert_not_called()
    handle.Close.assert_called_once_with()


def test_failed_handle_query_never_calls_quit(anchored_session):
    session, _, wait = anchored_session
    wait.side_effect = [-1]
    com = MagicMock()
    with pytest.raises(RuntimeError, match="Cannot inspect"):
        session.quit(com)
    com.Quit.assert_not_called()


def test_wait_pumps_com_messages(anchored_session):
    _, handle, wait = anchored_session
    wait.side_effect = [258, 0]
    assert sessions._wait_for_exit(handle, 1)
    sys.modules["pythoncom"].PumpWaitingMessages.assert_called_once_with()


def test_release_callback_runs_before_waiting_for_exit(anchored_session, monkeypatch):
    session, handle, wait = anchored_session
    wait.side_effect = [258]
    released = []

    def wait_after_release(actual_handle, timeout):
        assert released == [True]
        assert actual_handle is handle
        return True

    monkeypatch.setattr(sessions, "_wait_for_exit", wait_after_release)
    session.quit(MagicMock(), release=lambda: released.append(True))


def test_failed_com_quit_retains_references_for_retry(anchored_session):
    session, handle, wait = anchored_session
    wait.side_effect = [258]
    com = MagicMock()
    com.Quit.side_effect = OSError("busy")
    release = MagicMock()
    with pytest.raises(OSError, match="busy"):
        session.quit(com, release=release)
    release.assert_not_called()
    handle.Close.assert_not_called()


def test_exit_timeout_can_be_retried_without_quitting_again(anchored_session, monkeypatch):
    session, handle, wait = anchored_session
    wait.side_effect = [258, 0]
    monkeypatch.setattr(sessions, "_wait_for_exit", lambda *_: False)
    com = MagicMock()
    with pytest.raises(RuntimeError, match="did not exit"):
        session.quit(com)
    assert not session.closed
    handle.Close.assert_not_called()
    session.quit(com)
    com.Quit.assert_called_once_with()
    assert session.closed


@pytest.mark.parametrize("factory", ["powerpoint", "outlook"])
def test_facade_quits_verified_exclusive_process_after_owned_objects_close(monkeypatch, factory):
    session = MagicMock(pid=42, closed=False, _quit_requested=False)
    com = MagicMock()
    com.Presentations.Count = 0
    com.GetNamespace.return_value.CurrentProfileName = "UAT"
    com.GetNamespace.return_value.Stores.Count = 1
    if factory == "powerpoint":
        monkeypatch.setattr(ppt, "_start_office", lambda *_, **__: (com, session))
        monkeypatch.setattr(ppt, "_require_win32com", MagicMock())
        app = PowerPointApp.create(exclusive=True)
    else:
        monkeypatch.setattr(outlook, "_start_office", lambda *_, **__: (com, session))
        monkeypatch.setattr(outlook, "_configured_profiles", lambda: ["UAT"])
        monkeypatch.setattr(outlook, "_require_win32com", MagicMock())
        app = OutlookApp.start(exclusive=True)
    assert app.owns_process
    assert app.process_id == 42
    app.quit()
    session.quit.assert_called_once_with(com, release=app._release_com)


def test_unowned_powerpoint_document_blocks_exclusive_quit():
    com = MagicMock()
    com.Presentations.Count = 1
    session = MagicMock(closed=False, _quit_requested=False)
    app = PowerPointApp(com, _session=session)
    with pytest.raises(RuntimeError, match="Unowned presentations"):
        app.quit()
    session.quit.assert_not_called()


def test_already_exited_powerpoint_session_releases_proxy_without_com_calls():
    com = MagicMock()
    session = MagicMock()
    session.running.return_value = False
    app = PowerPointApp(com, _session=session)
    app.quit()
    assert app._com is None
    session.quit.assert_not_called()
    com.Quit.assert_not_called()


def test_exclusive_outlook_profile_failure_still_cleans_up_started_session(monkeypatch):
    com = MagicMock()
    com.GetNamespace.side_effect = OSError("profile inaccessible")
    session = MagicMock()
    monkeypatch.setattr(outlook, "_configured_profiles", lambda: ["UAT"])
    monkeypatch.setattr(outlook, "_require_win32com", MagicMock())
    monkeypatch.setattr(outlook, "_start_office", lambda *_, **__: (com, session))
    with pytest.raises(RuntimeError, match="MAPI profile unavailable"):
        OutlookApp.start(exclusive=True)
    assert session.quit.call_args.args == (com,)
    assert callable(session.quit.call_args.kwargs["release"])
