"""Headless tests for SAP value parsing and dead-session detection."""

from __future__ import annotations

import pytest

from dolphin_desktop._exceptions import ApplicationError, ElementNotFoundError
from dolphin_desktop._sap import (
    _find_all_in_tree,
    _find_in_tree,
    _is_scripting_security_dialog,
    _is_session_gone,
    _parse_selected_rows,
    _raise_if_session_gone,
)

# SelectedRows range syntax


def test_parse_selected_rows_single_indices() -> None:
    assert _parse_selected_rows("0,2,5") == [0, 2, 5]


def test_parse_selected_rows_expands_ranges() -> None:
    assert _parse_selected_rows("3,5-8,14") == [3, 5, 6, 7, 8, 14]


def test_parse_selected_rows_pure_range() -> None:
    assert _parse_selected_rows("2-4") == [2, 3, 4]


def test_parse_selected_rows_single_row_range() -> None:
    assert _parse_selected_rows("7-7") == [7]


def test_parse_selected_rows_tolerates_whitespace_and_blanks() -> None:
    assert _parse_selected_rows(" 1 , 3 - 5 , ") == [1, 3, 4, 5]


def test_parse_selected_rows_empty_string() -> None:
    assert _parse_selected_rows("") == []


def test_parse_selected_rows_ignores_garbage() -> None:
    assert _parse_selected_rows("1,abc,3") == [1, 3]


# Dead-session detection


class _FakeComError(Exception):
    """Shaped like pywintypes.com_error: hresult first in args."""


def test_is_session_gone_for_rpc_disconnected() -> None:
    exc = _FakeComError(-2147417848, "The object invoked has disconnected", None, None)
    assert _is_session_gone(exc) is True


def test_is_session_gone_for_server_unavailable() -> None:
    exc = _FakeComError(-2147023174, "The RPC server is unavailable", None, None)
    assert _is_session_gone(exc) is True


def test_is_session_gone_false_for_ordinary_failure() -> None:
    assert _is_session_gone(AttributeError("Busy")) is False
    assert _is_session_gone(_FakeComError(-2147352567, "Exception occurred")) is False


def test_is_session_gone_false_for_transient_call_rejected() -> None:
    # 0x80010001 RPC_E_CALL_REJECTED — the server is busy, not gone.
    assert _is_session_gone(_FakeComError(-2147418111, "Call was rejected")) is False


def test_is_session_gone_covers_every_server_died_hresult() -> None:
    """These are what SAP GUI actually returns once its process is killed."""
    for hresult, name in (
        (0x80010007, "RPC_E_SERVER_DIED"),
        (0x80010012, "RPC_E_SERVER_DIED_DNE"),
        (0x80010006, "RPC_E_CONNECTION_TERMINATED"),
        (0x80010108, "RPC_E_DISCONNECTED"),
        (0x800706BA, "RPC_S_SERVER_UNAVAILABLE"),
        (0x800706BE, "RPC_S_CALL_FAILED"),
        (0x800706BF, "RPC_S_CALL_FAILED_DNE"),
    ):
        exc = _FakeComError(hresult - 2**32, name)
        assert _is_session_gone(exc) is True, name


def test_server_fault_is_a_live_server_raising_not_a_dead_one() -> None:
    """0x80010105 RPC_E_SERVERFAULT — the server threw during THIS call.

    Treating it as a dead session aborts the whole test with "SAP session is
    no longer available" whenever a screen transition raises server-side.
    """
    exc = _FakeComError(0x80010105 - 2**32, "The server threw an exception")
    assert _is_session_gone(exc) is False


def test_raise_if_session_gone_raises_application_error() -> None:
    exc = _FakeComError(-2147417848, "disconnected", None, None)
    with pytest.raises(ApplicationError, match="no longer available"):
        _raise_if_session_gone(exc, "is_busy")


def test_raise_if_session_gone_passes_through_other_errors() -> None:
    # Not a dead-session COM failure, so the guard returns and leaves the
    # original exception for the caller to handle.
    assert _raise_if_session_gone(ValueError("nope"), "is_busy") is None


# Session readers must not report success once the session is gone


class _DeadSession:
    """COM stand-in whose every access raises a disconnected com_error."""

    def __getattr__(self, name: str):
        raise _FakeComError(-2147417848, "The object invoked has disconnected")


def test_is_busy_raises_instead_of_reporting_idle() -> None:
    from dolphin_desktop._sap import SapSession

    sess = SapSession(_DeadSession())
    with pytest.raises(ApplicationError):
        sess.is_busy()


def test_wait_until_ready_does_not_green_light_a_dead_session() -> None:
    from dolphin_desktop._sap import SapSession

    sess = SapSession(_DeadSession())
    with pytest.raises(ApplicationError):
        sess.wait_until_ready(timeout=0.1)


def test_assert_no_error_does_not_pass_on_a_dead_session() -> None:
    from dolphin_desktop._sap import SapSession

    sess = SapSession(_DeadSession())
    with pytest.raises(ApplicationError):
        sess.assert_no_error(timeout=0.1)


def test_title_and_system_info_raise_on_a_dead_session() -> None:
    from dolphin_desktop._sap import SapSession

    sess = SapSession(_DeadSession())
    for reader in (sess.title, sess.current_transaction, sess.system_info, sess.status_message):
        with pytest.raises(ApplicationError):
            reader()


def test_locator_resolution_does_not_downgrade_a_dead_session() -> None:
    """_wait_component polls is_busy() — the busiest path in the module.

    Catching the dead-session ApplicationError there rewrites it as an
    ElementNotFoundError after the full timeout, hiding the diagnosis.
    """
    from dolphin_desktop._sap import SapSession

    sess = SapSession(_DeadSession())
    locator = sess.locator(id="wnd[0]/usr/txtFOO").timeout(0.2)
    with pytest.raises(ApplicationError):
        locator._resolve()


# Scripting-security dialog identification


class _FakeWin32Gui:
    """The win32gui calls _is_scripting_security_dialog makes.

    ``statics`` are the dialog's label children — the identification path
    for locales that caption the notification with the front-end's own
    product name instead of "SAP GUI".
    """

    def __init__(
        self,
        *,
        title: str,
        cls: str = "#32770",
        visible: bool = True,
        statics: tuple[str, ...] = (),
    ) -> None:
        self._title = title
        self._cls = cls
        self._visible = visible
        self._statics = statics

    def IsWindowVisible(self, hwnd: int) -> bool:  # noqa: N802
        return self._visible

    def GetWindowText(self, hwnd: int) -> str:  # noqa: N802
        # Child handles are 1000+; the dialog itself is the hwnd passed in.
        if hwnd >= 1000:
            return self._statics[hwnd - 1000]
        return self._title

    def GetClassName(self, hwnd: int) -> str:  # noqa: N802
        if hwnd >= 1000:
            return "Static"
        return self._cls

    def EnumChildWindows(self, hwnd: int, callback, extra) -> None:  # noqa: N802
        for index in range(len(self._statics)):
            callback(1000 + index, extra)


def test_security_dialog_is_dismissed_when_the_owner_cannot_be_queried(monkeypatch) -> None:
    """OpenProcess returns ACCESS_DENIED for an elevated or cross-user SAP GUI.

    Refusing on that leaves the modal up and the run stalls behind it with
    no diagnostic at all, so the caption + class evidence has to carry it.
    """
    from dolphin_desktop import _sap

    monkeypatch.setattr(_sap, "_window_process_name", lambda hwnd: None)
    dialog = _FakeWin32Gui(title="SAP GUI Security")
    assert _is_scripting_security_dialog(1, dialog) is True


def test_unqueryable_owner_does_not_relax_the_caption_and_class_filter(monkeypatch) -> None:
    from dolphin_desktop import _sap

    monkeypatch.setattr(_sap, "_window_process_name", lambda hwnd: None)
    assert _is_scripting_security_dialog(1, _FakeWin32Gui(title="Save changes?")) is False
    assert (
        _is_scripting_security_dialog(1, _FakeWin32Gui(title="SAP GUI Security", cls="Edit"))
        is False
    )


def test_localized_dialog_is_identified_by_its_body(monkeypatch) -> None:
    """A Polish SAP Logon 800 captions the notification "SAP Logon".

    Observed live: caption "SAP Logon", body "Próba dostępu skryptu do
    SAP GUI.", buttons &OK / &Zaniechanie. Matching the caption alone
    left it on screen, and because the dialog is modal to the scripting
    call, the attach blocked until a human clicked it.
    """
    from dolphin_desktop import _sap

    monkeypatch.setattr(_sap, "_window_process_name", lambda hwnd: "saplogon.exe")
    dialog = _FakeWin32Gui(
        title="SAP Logon",
        statics=("Próba dostępu skryptu do SAP GUI. ",),
    )
    assert _is_scripting_security_dialog(1, dialog) is True


def test_front_end_main_window_is_not_mistaken_for_the_notification(monkeypatch) -> None:
    """The SAP Logon launcher is also a #32770 owned by a SAP process."""
    from dolphin_desktop import _sap

    monkeypatch.setattr(_sap, "_window_process_name", lambda hwnd: "saplogon.exe")
    launcher = _FakeWin32Gui(title="SAP Logon 800", statics=("Connections", "Footer"))
    assert _is_scripting_security_dialog(1, launcher) is False


def test_dialog_owned_by_another_program_is_refused(monkeypatch) -> None:
    from dolphin_desktop import _sap

    monkeypatch.setattr(_sap, "_window_process_name", lambda hwnd: "notepad.exe")
    assert _is_scripting_security_dialog(1, _FakeWin32Gui(title="SAP GUI Security")) is False


class _Stub:
    """ctypes function stub that tolerates restype / argtypes assignment."""

    def __init__(self, fn) -> None:
        self._fn = fn
        self.restype = None
        self.argtypes: list = []

    def __call__(self, *args):
        return self._fn(*args)


class _FakeWindll:
    def __init__(self, image_path: str, *, pid: int = 4321, open_ok: bool = True) -> None:
        self.attempts: list[int] = []
        self.closed: list[int] = []

        def _thread_pid(hwnd, pid_ref):
            pid_ref._obj.value = pid
            return 99

        def _open(access, inherit, target_pid):
            return 0x1234 if open_ok else 0

        def _query(handle, flags, buf, size_ref):
            capacity = size_ref._obj.value
            self.attempts.append(capacity)
            if len(image_path) + 1 > capacity:
                return 0  # ERROR_INSUFFICIENT_BUFFER
            buf.value = image_path
            size_ref._obj.value = len(image_path)
            return 1

        self.user32 = type("_U", (), {"GetWindowThreadProcessId": _Stub(_thread_pid)})()
        self.kernel32 = type(
            "_K",
            (),
            {
                "OpenProcess": _Stub(_open),
                "QueryFullProcessImageNameW": _Stub(_query),
                "CloseHandle": _Stub(lambda h: self.closed.append(h) or 1),
            },
        )()


def _process_name(monkeypatch, windll: _FakeWindll) -> str | None:
    import ctypes

    from dolphin_desktop._sap import _window_process_name

    monkeypatch.setattr(ctypes, "windll", windll)
    return _window_process_name(1)


def test_process_name_reads_a_normal_image_path(monkeypatch) -> None:
    windll = _FakeWindll(r"C:\Program Files\SAP\FrontEnd\SAPgui\saplogon.exe")
    assert _process_name(monkeypatch, windll) == "saplogon.exe"
    assert windll.attempts == [260]
    assert windll.closed == [0x1234]


def test_process_name_grows_the_buffer_past_max_path(monkeypatch) -> None:
    """A path over MAX_PATH fails the first call with ERROR_INSUFFICIENT_BUFFER."""
    long_path = "C:\\" + "\\".join(["segment"] * 60) + "\\saplogon.exe"
    assert len(long_path) > 260
    windll = _FakeWindll(long_path)
    assert _process_name(monkeypatch, windll) == "saplogon.exe"
    assert windll.attempts[0] == 260
    assert len(windll.attempts) == 2


def test_process_name_reports_unknown_rather_than_not_sap(monkeypatch) -> None:
    """ACCESS_DENIED must not read as "the owner is not SAP"."""
    windll = _FakeWindll("saplogon.exe", open_ok=False)
    assert _process_name(monkeypatch, windll) is None


# Tree-walk cycle guard


class _Component:
    """SAP component stand-in; ``comp_id=None`` makes .Id raise like a COM error."""

    def __init__(self, type: str, name: str, comp_id: str | None = None, children=()) -> None:
        self.Type = type
        self.Name = name
        self.Children = list(children)
        self._comp_id = comp_id

    @property
    def Id(self) -> str:  # noqa: N802
        if self._comp_id is None:
            raise AttributeError("Id")
        return self._comp_id


def _idless_screen() -> _Component:
    return _Component(
        "GuiUserArea",
        "usr",
        children=[_Component("GuiButton", "A"), _Component("GuiButton", "B")],
    )


def test_walk_never_dedupes_on_python_object_identity() -> None:
    """win32com reuses freed wrappers' addresses, so id() marks live siblings
    as already-visited and silently drops them."""
    seen: set[str] = set()
    result: list = []
    _find_all_in_tree(_idless_screen(), type="GuiButton", seen=seen, depth=5, result=result)
    assert [c.Name for c in result] == ["A", "B"]
    assert seen == set()


def test_find_in_tree_reaches_a_sibling_with_no_id() -> None:
    found = _find_in_tree(_idless_screen(), name="B", type=None, seen=set(), depth=5)
    assert found is not None and found.Name == "B"


def test_walk_still_dedupes_on_the_sap_component_id() -> None:
    shared = _Component("GuiButton", "Save", "wnd[0]/tbar[0]/btn[11]")
    root = _Component("GuiUserArea", "usr", "wnd[0]/usr", children=[shared, shared])
    result: list = []
    _find_all_in_tree(root, type="GuiButton", seen=set(), depth=5, result=result)
    assert len(result) == 1


class _CountingComponent(_Component):
    """Counts .Id reads — each one is a COM round-trip to the SAP GUI."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.id_reads = 0

    @property
    def Id(self) -> str:  # noqa: N802
        self.id_reads += 1
        return _Component.Id.fget(self)  # type: ignore[attr-defined]


def test_enumerate_screen_types_reads_each_component_id_once() -> None:
    """The cycle guard and the reported id come from the same read; two of
    them doubles the round-trips on a full-screen dump."""
    from dolphin_desktop._sap import SapSession

    child = _CountingComponent("GuiButton", "Save", "wnd[0]/usr/btnSAVE")
    root = _CountingComponent("GuiUserArea", "usr", "wnd[0]/usr", children=[child])

    class _Session:
        Busy = False

        def FindById(self, component_id: str):  # noqa: N802
            return root

    entries = SapSession(_Session()).enumerate_screen_types()
    assert [e["id"] for e in entries] == ["wnd[0]/usr", "wnd[0]/usr/btnSAVE"]
    assert (root.id_reads, child.id_reads) == (1, 1)


def test_locator_still_reports_a_missing_component_as_not_found() -> None:
    """The dead-session escape hatch must not swallow ordinary misses."""
    from dolphin_desktop._sap import SapSession

    class _EmptySession:
        def FindById(self, component_id: str):  # noqa: N802
            raise LookupError(component_id)

        Busy = False

    sess = SapSession(_EmptySession())
    with pytest.raises(ElementNotFoundError):
        sess.locator(id="wnd[0]/usr/txtNOPE").timeout(0.1)._resolve()
