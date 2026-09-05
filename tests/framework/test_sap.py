"""Tests for the SAP GUI facade.

The real SAP COM server is deliberately not needed here.  These tests model
the small collection/property/method shapes exposed by SAP GUI Scripting and
exercise the public fallback behaviour of :mod:`dolphin_desktop._sap`.
"""

# SAP GUI's COM API intentionally uses PascalCase member names.
# ruff: noqa: N802

from __future__ import annotations

import builtins
import itertools
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dolphin_desktop import _sap as sap
from dolphin_desktop._exceptions import ApplicationError, ElementNotFoundError, WaitTimeoutError
from dolphin_desktop._sap import _call_first, _collection_count, _iter_children, _normalize_vkey


class Collection:
    def __init__(self, *items, count: bool = True):
        self.items = list(items)
        if count:
            self.Count = len(self.items)

    def __call__(self, index: int):
        return self.items[index]

    def __len__(self):
        return len(self.items)


class ItemCollection(Collection):
    def __call__(self, index: int):
        raise TypeError("COM collection is not callable")

    def Item(self, index: int):
        return self.items[index]


class RejectWrites:
    def __init__(self, values: dict[str, object] | None = None, rejected=()):
        object.__setattr__(self, "_values", values or {})
        object.__setattr__(self, "_rejected", set(rejected))

    def __getattr__(self, name: str):
        values = object.__getattribute__(self, "_values")
        if name in values:
            return values[name]
        raise AttributeError(name)

    def __setattr__(self, name: str, value: object) -> None:
        if name in object.__getattribute__(self, "_rejected"):
            raise AttributeError(name)
        object.__getattribute__(self, "_values")[name] = value


@pytest.fixture(autouse=True)
def no_polling(monkeypatch):
    """Keep retry-path tests deterministic and instantaneous."""
    monkeypatch.setattr(sap, "_sleep", lambda: None)


def make_session(component=None):
    session = sap.SapSession(SimpleNamespace(Busy=False))
    session.wait_until_ready = Mock(return_value=session)
    if component is None:
        component = SimpleNamespace()
    locator = sap.SapLocator(session, id="component", timeout=0.1)
    locator._resolve = Mock(return_value=component)
    session._wait_component = Mock(return_value=component)
    return session, locator


def test_stopwatch_and_low_level_helpers(monkeypatch):
    ticks = iter([10.0, 12.5, 20.0, 21.0])
    monkeypatch.setattr(sap.time, "monotonic", lambda: next(ticks))
    watch = sap.Stopwatch()
    assert watch.elapsed == 2.5
    watch.reset()
    assert watch.elapsed == 1.0

    assert sap._collection_count(ItemCollection("a")) == 1
    assert sap._collection_count(SimpleNamespace()) is None
    assert list(sap._iter_children(SimpleNamespace(Children=ItemCollection("a", "b")))) == [
        "a",
        "b",
    ]

    class IterableChildren:
        Children = iter(["x", "y"])

    assert list(sap._iter_children(IterableChildren())) == ["x", "y"]
    assert sap._iter_children(SimpleNamespace(Children=object())) == []
    assert sap._set_first(RejectWrites(rejected=("a", "b")), ("a", "b"), 1) is False
    assert sap._component_text(SimpleNamespace(value=7)) == "7"
    assert sap._component_text(SimpleNamespace()) == ""
    assert sap._get_scripting_engine(SimpleNamespace(GetScriptingEngine="engine")) == "engine"


def test_process_and_dialog_helpers_cover_unknown_and_exception_paths(monkeypatch):
    import ctypes

    class ApiCall:
        def __init__(self, fn):
            self.fn = fn

        def __call__(self, *args):
            return self.fn(*args)

    class User32:
        GetWindowThreadProcessId = ApiCall(lambda *args: None)

    class Kernel32:
        OpenProcess = ApiCall(lambda *args: None)
        QueryFullProcessImageNameW = ApiCall(lambda *args: False)
        CloseHandle = ApiCall(lambda *args: True)

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=User32(), kernel32=Kernel32()))
    assert sap._window_process_name(1) is None

    class BadGui:
        def EnumChildWindows(self, *_):
            raise RuntimeError("window vanished")

    assert sap._dialog_static_texts(1, BadGui()) == []

    class MixedGui:
        def GetClassName(self, child):
            if child == 1:
                return "Button"
            raise RuntimeError("gone")

        def GetWindowText(self, child):
            return "ignored"

        def EnumChildWindows(self, hwnd, callback, extra):
            callback(1, extra)
            callback(2, extra)

    assert sap._dialog_static_texts(1, MixedGui()) == []

    class DialogGui:
        def IsWindowVisible(self, hwnd):
            return False

    assert sap._is_scripting_security_dialog(1, DialogGui()) is False


def test_scripting_worker_dismisses_only_allow_buttons(monkeypatch):
    sent = []
    api = SimpleNamespace(SendMessage=lambda *args: sent.append(args))
    con = SimpleNamespace(BM_CLICK=123)

    class Gui:
        def EnumWindows(self, callback, extra):
            callback(10, extra)

        def EnumChildWindows(self, hwnd, callback, extra):
            for child, text in ((1, "&Allow"), (2, "Cancel"), (3, " OK ")):
                self.text = text
                callback(child, extra)

        def GetWindowText(self, child):
            return {1: "&Allow", 2: "Cancel", 3: " OK "}[child]

    gui = Gui()
    modules = {"win32api": api, "win32con": con, "win32gui": gui}
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sap, "_is_scripting_security_dialog", lambda hwnd, win32gui: True)
    monkeypatch.setattr(sap.time, "sleep", lambda _: None)

    class Stop:
        calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 1

    sap._scripting_security_worker(Stop())
    assert sent == [(1, 123, 0, 0), (3, 123, 0, 0)]


def test_require_com_and_scripting_engine_fallback(monkeypatch):
    assert (
        sap._get_scripting_engine(SimpleNamespace(GetScriptingEngine=lambda: "created"))
        == "created"
    )

    original_import = builtins.__import__

    def fail_win32com(name, *args, **kwargs):
        if name == "win32com.client":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_win32com)
    with pytest.raises(RuntimeError, match="win32com is not available"):
        sap._require_win32com()


def test_sap_gui_connect_backend_login_and_collections(monkeypatch):
    backend = Mock()
    backend.supports.return_value = True
    monkeypatch.setattr(sap.SapGui, "backend", classmethod(lambda cls: backend))
    assert sap.SapGui.backend_supports("x") is True
    sap.SapGui.require_capability("y")
    backend.require_capability.assert_called_once_with("y")

    session_com = SimpleNamespace(Busy=False)
    conn = SimpleNamespace(
        Description="QA",
        Children=Collection(session_com),
        CloseConnection=Mock(),
    )
    raw = SimpleNamespace(Children=Collection(conn), Version="7700")
    gui = sap.SapGui(raw)
    assert gui.raw is raw and gui.version == "7700"
    assert len(gui.connections()) == 1
    gui.close_all_connections()
    conn.CloseConnection.assert_called_once_with()
    assert gui.session().raw is session_com

    monkeypatch.setattr(
        sap,
        "_require_win32com",
        lambda: SimpleNamespace(GetObject=lambda _: SimpleNamespace(GetScriptingEngine=raw)),
    )
    assert sap.SapGui.connect(timeout=0).raw is raw

    class LoginApp:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def connect(self, **kwargs):
            self.connected = kwargs

        def window(self, **kwargs):
            return SimpleNamespace(set_focus=Mock())

    sent = []
    monkeypatch.setitem(sys.modules, "pywinauto", SimpleNamespace(Application=LoginApp))
    monkeypatch.setitem(
        sys.modules,
        "pywinauto.keyboard",
        SimpleNamespace(send_keys=lambda *args, **kwargs: sent.append((args, kwargs))),
    )
    monkeypatch.setattr(sap.time, "sleep", lambda _: None)
    sap.SapGui.keyboard_login("SAP Logon", "user", "pass")
    assert [item[0][0] for item in sent] == ["user", "{TAB}", "pass", "{ENTER}"]


def test_sap_gui_failure_and_open_connection_fallbacks(monkeypatch):
    monkeypatch.setattr(sap, "_require_win32com", Mock(side_effect=RuntimeError("offline")))
    with pytest.raises(ApplicationError, match="Could not connect"):
        sap.SapGui.connect(timeout=0)

    raw = SimpleNamespace(Children=Collection(), OpenConnection=Mock())
    gui = sap.SapGui(raw)
    with pytest.raises(WaitTimeoutError, match="No SAP connection"):
        gui.wait_for_any_connection(timeout=0)

    bad_app = SimpleNamespace(
        Children=SimpleNamespace(Count=0),
        OpenConnection=Mock(return_value=SimpleNamespace(Children=Collection())),
    )
    assert sap.SapGui(bad_app).open_connection("QA", timeout=0) is None

    session = SimpleNamespace(Busy=False)
    fallback_conn = SimpleNamespace(Children=Collection(session))
    app = SimpleNamespace(
        Children=Collection(fallback_conn),
        OpenConnection=Mock(return_value=SimpleNamespace(Children=Collection())),
    )
    assert sap.SapGui(app).open_connection("QA", timeout=0).raw is session

    app_instance = SimpleNamespace(connect=Mock(side_effect=RuntimeError("no window")))
    monkeypatch.setattr(sys.modules["pywinauto"], "Application", Mock(return_value=app_instance))
    with pytest.raises(ApplicationError, match="Cannot connect"):
        sap.SapGui.keyboard_login("missing", "u", "p")


def test_connection_fallbacks_and_session_lifecycle(monkeypatch):
    session_com = SimpleNamespace(CreateSession=Mock())

    class BrokenDescription:
        Children = Collection(session_com)

        @property
        def Description(self):
            raise RuntimeError("description unavailable")

    conn_com = BrokenDescription()
    conn = sap.SapConnection(conn_com)
    assert conn.raw is conn_com and conn.description == ""
    conn.close()
    conn_com.CloseConnection = Mock(side_effect=RuntimeError())
    conn.close()
    assert conn.session().raw is session_com
    assert len(conn.sessions()) == 1
    conn.create_session()
    session_com.CreateSession.assert_called_once_with()
    conn_com.Children = SimpleNamespace()
    assert conn.sessions() == []
    with pytest.raises(ApplicationError, match="does not expose CreateSession"):
        sap.SapConnection(SimpleNamespace(Children=Collection(SimpleNamespace()))).create_session()
    with pytest.raises(ApplicationError, match="Cannot create"):
        sap.SapConnection(SimpleNamespace(Children=object())).create_session()

    empty_conn = sap.SapConnection(SimpleNamespace(Children=Collection()))
    with pytest.raises(WaitTimeoutError, match="Session count"):
        empty_conn.wait_for_session_count(1, timeout=0)
    ready_conn = sap.SapConnection(SimpleNamespace(Children=Collection(session_com)))
    ready_conn.wait_for_session_count(1, timeout=0)

    session = sap.SapSession(SimpleNamespace(Busy=False))
    assert session.raw.Busy is False
    assert session.find_by_id("x")._id == "x"
    with pytest.raises(ValueError):
        session.locator()
    assert session.locator(name="n")._name == "n"


def test_session_waits_readers_and_popup_actions(monkeypatch):
    window = SimpleNamespace(Text="SAP Easy Access", SendVKey=Mock(), Children=Collection())
    sbar = SimpleNamespace(Text="done", MessageType="S")
    info = SimpleNamespace(Transaction="SE16", User="U", Client="001", SystemName="QAS")
    by_id = {"wnd[0]": window, "wnd[0]/sbar": sbar}
    com = SimpleNamespace(Busy=False, Info=info, FindById=lambda key: by_id[key])
    session = sap.SapSession(com)
    assert session.wait_until_ready(timeout=0) is session
    assert session.wait_for_title("Easy", timeout=0) is session
    assert session.wait_for_title("SAP Easy Access", exact=True, timeout=0) is session
    with pytest.raises(ValueError, match="always matches"):
        session.wait_for_title("")
    with pytest.raises(WaitTimeoutError):
        session.wait_for_title("Missing", timeout=0)
    assert session.is_busy() is False
    assert session.current_transaction() == "SE16"
    assert session.title() == "SAP Easy Access"
    assert session.status_message() == ("done", "S")
    assert session.system_info()["client"] == "001"

    child = SimpleNamespace(Press=Mock())
    by_id["wnd[1]"] = SimpleNamespace(Children=Collection(child), SendVKey=Mock())
    session.confirm_popup(timeout=0)
    child.Press.assert_called_once_with()
    session.find_by_id = Mock(return_value=SimpleNamespace(click=Mock()))
    session.confirm_popup(button_id="button", timeout=0)
    assert session.close_popup(timeout=0) is session
    by_id["wnd[1]"].SendVKey.assert_called_with(12)

    by_id.pop("wnd[1]")
    window.SendVKey.reset_mock()
    session.confirm_popup(timeout=0)
    session.close_popup(timeout=0)
    assert window.SendVKey.call_args_list[-1].args == (12,)
    assert session.enter() is session
    assert session.execute() is session
    assert session.save() is session
    assert session.navigate_back() is session
    assert session.cancel() is session
    assert [call.args[0] for call in window.SendVKey.call_args_list][-5:] == [0, 8, 11, 3, 12]


def test_session_assertions_navigation_and_search_helpers(monkeypatch):
    session = sap.SapSession(SimpleNamespace(Busy=False))
    session.wait_until_ready = Mock(return_value=session)
    session.find_by_id = Mock(return_value=SimpleNamespace(text=Mock(return_value="Hello")))
    assert session.assert_field_value("field", "hello") is session
    assert session.assert_field_value("field", "ell", partial=True) is session
    with pytest.raises(ValueError, match="always matches"):
        session.assert_field_value("field", "", partial=True)
    with pytest.raises(AssertionError):
        session.assert_field_value("field", "other")
    session.status_message = Mock(return_value=("ok", "S"))
    assert session.assert_no_error() is session
    session.status_message.return_value = ("bad", "E")
    with pytest.raises(AssertionError, match="reported an error"):
        session.assert_no_error()
    session.status_message.side_effect = [("bad", "E"), ("ok", "S")]
    assert session.wait_until_no_error(timeout=1) is session
    session.status_message.side_effect = [("bad", "E"), ("bad", "E")]
    with pytest.raises(WaitTimeoutError, match="still shows error"):
        session.wait_until_no_error(timeout=0)

    session.find_by_id = Mock(return_value=SimpleNamespace(set_text=Mock()))
    session.send_vkey = Mock(return_value=session)
    assert session.transaction(" SE16 ") is session
    assert session.transaction("/nSE16") is session
    with pytest.raises(ValueError):
        session.transaction(" ")
    assert session.find_by_id.call_count == 2

    session.dismiss_all_popups = Mock(return_value=session)
    session.logoff(confirm=True)
    session.transaction = Mock(side_effect=RuntimeError("closed"))
    session.logoff(confirm=False)

    session.transaction = Mock(return_value=session)
    session.execute = Mock(return_value=session)
    program = SimpleNamespace(set_text=Mock())
    variant = SimpleNamespace(exists=Mock(return_value=True), set_text=Mock())
    session.find_by_id_or_variant = Mock(return_value=program)
    session.find_by_id = Mock(side_effect=[variant])
    assert session.execute_report("REPORT", variant="V1") is session
    program.set_text.assert_called_once_with("REPORT")
    variant.set_text.assert_called_once_with("V1")

    root = SimpleNamespace(
        Id="root",
        Type="GuiMainWindow",
        Children=Collection(
            SimpleNamespace(Id="b1", Type="GuiButton", Text="One"),
            SimpleNamespace(Id="b2", Type="GuiButton", Text="Two"),
        ),
    )
    session.is_busy = Mock(return_value=False)
    session._find_now = Mock(return_value=root)
    first = session.find_first_in_tree("GuiButton", timeout=0)
    assert first._id == "b1"
    assert [x._id for x in session.find_all_in_tree("GuiButton", timeout=0)] == ["b1", "b2"]
    session._find_now = Mock(side_effect=AttributeError("missing"))
    with pytest.raises(ElementNotFoundError):
        session.find_first_in_tree("GuiButton", timeout=0)
    with pytest.raises(ElementNotFoundError):
        session.find_all_in_tree("GuiButton", timeout=0)

    session._find_now = Mock(side_effect=[AttributeError("a"), object()])
    session.find_by_id_or_variant = sap.SapSession.find_by_id_or_variant.__get__(session)
    assert session.find_by_id_or_variant("a", "b", timeout=0)._id == "b"
    session._find_now = Mock(side_effect=AttributeError("missing"))
    with pytest.raises(ElementNotFoundError):
        session.find_by_id_or_variant("a", timeout=0)


def test_session_menus_tabs_window_and_selection_helpers(monkeypatch):
    session = sap.SapSession(SimpleNamespace(Busy=False))
    session.wait_until_ready = Mock(return_value=session)
    session.is_busy = Mock(return_value=False)
    tab = SimpleNamespace(Text="Details", Select=Mock())
    tab_strip = SimpleNamespace(Type="GuiTabStrip", Id="tabs", Children=Collection(tab))
    root = SimpleNamespace(Id="root", Type="GuiMainWindow", Children=Collection(tab_strip))
    session._find_now = Mock(return_value=root)
    assert session.click_tab_by_label("tail", timeout=0) is session
    tab.Select.assert_called_once_with()

    tab.Select.side_effect = RuntimeError("old API")
    tab.Press = Mock()
    assert (
        session.click_tab_by_label("Details", partial=False, case_sensitive=True, timeout=0)
        is session
    )
    tab.Press.assert_called_once_with()

    menu_item = SimpleNamespace(Text="Delete", Select=Mock())
    menu = SimpleNamespace(
        Children=Collection(SimpleNamespace(Text="Program", Children=Collection(menu_item)))
    )
    session._find_now = Mock(return_value=menu)
    assert session.menu_click("Program > Delete", timeout=0) is session
    session.menu_click(["Program", "Delete"], partial=False, case_sensitive=True, timeout=0)
    with pytest.raises(ValueError):
        session.menu_click([])
    menu_item.Select = None
    with pytest.raises(ElementNotFoundError):
        session.menu_click("Nope", timeout=0)

    wnd = SimpleNamespace(Maximize=Mock(), Restore=Mock(), Width=0, Height=0)
    session._find_now = Mock(return_value=wnd)
    assert session.maximize().restore().set_window_size(800, 600) is session
    assert (wnd.Width, wnd.Height) == (800, 600)
    assert session.close() is None
    session._find_now = Mock(side_effect=RuntimeError("gone"))
    assert session.restore() is session
    with pytest.raises(ApplicationError, match="Cannot resize"):
        session.set_window_size(1, 2)

    session._com = SimpleNamespace(CreateSession=Mock())
    session.create_session()
    with pytest.raises(ApplicationError):
        sap.SapSession(SimpleNamespace()).create_session()
    session._com = SimpleNamespace(Info=SimpleNamespace(User="u", Client="", SystemName="s"))
    assert session.system_info()["user"] == "u"
    session._find_now = Mock(return_value=object())
    assert session.wait_for_popup(timeout=0) is session
    session._find_now = Mock(side_effect=AttributeError("not yet"))
    with pytest.raises(WaitTimeoutError):
        session.wait_for_popup(timeout=0)

    button = SimpleNamespace(Id="yes", Type="GuiButton", Text="Yes", Press=Mock())
    popup = SimpleNamespace(Id="popup", Type="GuiModalWindow", Children=Collection(button))
    session._find_now = Mock(return_value=popup)
    assert session.popup_click_button("ye", timeout=0) is session
    assert session.click_toolbar_button("Yes", timeout=0) is session

    session.find_by_id = Mock(return_value=SimpleNamespace(set_text=Mock(), click=Mock()))
    session._find_now = Mock(return_value=object())
    assert session.set_selection_value("S_X", "v") is session
    session._selection_field_id = Mock(side_effect=["low", "high"])
    assert session.set_selection_range("S_X", low="l", high="h") is session
    session._selection_field_id = Mock(return_value=None)
    with pytest.raises(ElementNotFoundError):
        session.set_selection_value("MISSING", "v")
    with pytest.raises(ElementNotFoundError):
        session.set_selection_range("MISSING", low="v")
    btn = object()
    session._find_now = Mock(return_value=btn)
    session.find_by_id = Mock(return_value=SimpleNamespace(click=Mock()))
    assert session.open_selection_options("S_X") is session
    session._find_now = Mock(side_effect=AttributeError("missing"))
    with pytest.raises(ElementNotFoundError):
        session.open_selection_options("S_X")


def test_session_screenshot_status_f4_and_enumeration(monkeypatch):
    session = sap.SapSession(SimpleNamespace(Busy=False))
    session.wait_until_ready = Mock(return_value=session)
    wnd = SimpleNamespace(ScreenLeft=10, ScreenTop=20, Width=100, Height=50)
    session._find_now = Mock(return_value=wnd)
    image = Mock()
    screen = SimpleNamespace(screenshot=Mock(return_value=image))
    monkeypatch.setattr("dolphin_desktop._image.Screen", screen)
    assert session.screenshot("out.png") is image
    image.save.assert_called_once_with("out.png")
    session._find_now = Mock(side_effect=RuntimeError("no bounds"))
    assert session.screenshot() is image
    assert screen.screenshot.call_args_list[-1].kwargs == {"region": None}

    session.status_message = Mock(side_effect=[("", ""), ("Saved", "S")])
    assert session.wait_for_status_message(timeout=1) == ("Saved", "S")
    session.status_message.side_effect = None
    session.status_message.return_value = ("Error", "E")
    assert session.wait_for_status_message(type="E", timeout=0) == ("Error", "E")
    with pytest.raises(WaitTimeoutError):
        session.wait_for_status_message("missing", timeout=0)
    session.status_message.return_value = ("saved", "S")
    assert session.assert_status("SAV", type="S") is session
    session.status_message.return_value = ("no", "E")
    with pytest.raises(AssertionError):
        session.assert_status(type="S")

    value = SimpleNamespace(Id="field", Type="GuiCTextField", Text="", Children=Collection())
    popup = SimpleNamespace(
        Id="p", Type="GuiModalWindow", Children=Collection(value), SendVKey=Mock()
    )
    session._find_now = Mock(return_value=popup)
    assert session.select_f4_value("abc", timeout=0) is session
    assert value.Text == "abc"
    assert popup.SendVKey.called

    grid = SimpleNamespace(
        Id="grid",
        Type="GuiGridView",
        RowCount=2,
        Columns=Collection(SimpleNamespace(Name="KEY")),
        GetCellValue=Mock(side_effect=lambda row, col: "hit" if row == 1 else "no"),
        SetCurrentCell=Mock(),
        ClickCurrentCell=Mock(),
        Children=Collection(),
    )
    popup.Children = Collection(grid)
    session.dismiss_all_popups = Mock(return_value=session)
    assert session.select_f4_value("hit", timeout=0) is session
    session._find_now = Mock(return_value=popup)
    with pytest.raises(ElementNotFoundError):
        session.select_f4_value("missing", column="KEY", timeout=0)

    child = SimpleNamespace(
        Id="c", Type="GuiShell", SubType="GridView", Name="shell", Children=Collection()
    )
    root = SimpleNamespace(Id="r", Type="GuiMainWindow", Name="root", Children=Collection(child))
    session._find_now = Mock(return_value=root)
    entries = session.enumerate_screen_types()
    assert entries[-1]["sub_type"] == "GridView"
    session._find_now = Mock(side_effect=RuntimeError())
    assert session.enumerate_screen_types() == []


def test_locator_core_actions_and_states():
    component = SimpleNamespace(
        Text="hello",
        Selected=False,
        Visible=True,
        Enabled=True,
        Changeable=True,
        Required=True,
        ReadOnly=False,
        Tooltip="tip",
        Press=Mock(),
        DoubleClick=Mock(),
        SetFocus=Mock(),
    )
    session, loc = make_session(component)
    session.send_vkey = Mock(return_value=session)
    assert loc.raw is component
    assert loc.click().double_click().set_text("x").type_text("y").clear() is loc
    assert component.Text == ""
    assert loc.focus().press_key("F8") is loc
    assert loc.text() == ""
    assert loc.value() == ""
    assert loc.exists() and loc.is_visible() and loc.is_enabled()
    assert loc.is_checked() is False and loc.set_checked(True).is_checked() is True
    assert loc.is_mandatory() and not loc.is_readonly() and loc.get_tooltip() == "tip"
    assert loc.timeout(1)._timeout == 1
    assert "SapLocator" in repr(loc)

    component.Visible = False
    component.Enabled = False
    component.Changeable = False
    component.ReadOnly = True
    assert not loc.is_visible() and not loc.is_enabled()
    assert loc.wait_for(state="hidden", timeout=0) is loc
    assert loc.wait_until_hidden(0) is loc
    component.Visible = True
    component.Enabled = True
    component.Changeable = True
    component.ReadOnly = False
    assert loc.wait_for(state="visible", timeout=0) is loc
    assert loc.wait_for(state="enabled", timeout=0) is loc
    with pytest.raises(ValueError):
        loc.wait_for(state="unknown", timeout=0)

    missing, bad = make_session(SimpleNamespace())
    missing._wait_component = Mock(side_effect=RuntimeError("missing"))
    bad._resolve = Mock(side_effect=RuntimeError("missing"))
    assert not bad.exists() and not bad.is_visible() and not bad.is_enabled()
    assert not bad.is_checked() and not bad.is_mandatory() and not bad.is_readonly()
    assert bad.get_tooltip() == ""

    component.Press.side_effect = RuntimeError("rejected")
    with pytest.raises(RuntimeError):
        loc.click()
    component.Press.side_effect = None
    del component.DoubleClick
    with pytest.raises(ApplicationError):
        loc.double_click()
    no_text = RejectWrites(rejected=("Text", "text", "Value", "value"))
    _, no_text_loc = make_session(no_text)
    with pytest.raises(ApplicationError):
        no_text_loc.set_text("x")
    no_focus = SimpleNamespace()
    _, no_focus_loc = make_session(no_focus)
    with pytest.raises(ApplicationError):
        no_focus_loc.focus()
    with pytest.raises(ApplicationError):
        no_focus_loc.press_key("F1")


def test_locator_attributes_waits_checked_and_rows():
    component = SimpleNamespace(
        Text="abc", Selected=None, RowCount=2, GetCellValue=Mock(return_value="v")
    )
    session, loc = make_session(component)
    assert loc.get_attribute("text") == "abc"
    with pytest.raises(AttributeError):
        loc.get_attribute("missing")
    assert loc.get_attribute("missing", "default") == "default"
    session.is_busy = Mock(return_value=False)
    component.Visible = False
    assert loc.wait_for(state="invisible", timeout=0) is loc
    with pytest.raises(WaitTimeoutError):
        loc.wait_for(state="visible", timeout=0)
    assert loc.row_count() == 2
    assert loc.cell_value(0, "C") == "v"
    assert loc.get_selected_rows() == []
    component.SelectedRows = "1,3-4"
    assert loc.get_selected_rows() == [1, 3, 4]

    rows = Collection(SimpleNamespace(Item=lambda col: SimpleNamespace(Text="table")))
    table = SimpleNamespace(Rows=rows, RowCount=None)
    _, table_loc = make_session(table)
    assert table_loc.row_count() == 1 and table_loc.cell_value(0, 1) == "table"
    assert table_loc.cell_value(0, 0) == "table"

    columns = Collection(SimpleNamespace(Name="NAME"))
    fallback = SimpleNamespace(
        Columns=columns,
        ColumnOrder=Collection("ORDER"),
        GetCellValue=Mock(side_effect=RuntimeError()),
        Rows=rows,
    )
    _, fallback_loc = make_session(fallback)
    assert fallback_loc.cell_value(0, 0) == "table"
    impossible = SimpleNamespace(GetCellValue=Mock(side_effect=RuntimeError()), Rows=Collection())
    _, impossible_loc = make_session(impossible)
    with pytest.raises(ApplicationError):
        impossible_loc.cell_value(0, 0)

    class RowControl:
        def __init__(self):
            self.Selected = False

        def __setattr__(self, name, value):
            if name == "SelectedRows":
                raise AttributeError(name)
            object.__setattr__(self, name, value)

        def GetAbsoluteRow(self, index):
            return self

    row_control = RowControl()
    _, row_loc = make_session(row_control)
    assert row_loc.select_row(2) is row_loc and row_control.Selected
    fallback_select = RejectWrites(rejected=("SelectedRows",))
    fallback_select.GetAbsoluteRow = lambda index: row_control
    _, fallback_row_loc = make_session(fallback_select)
    assert fallback_row_loc.select_row(1) is fallback_row_loc
    impossible_select = RejectWrites(rejected=("SelectedRows",))
    _, impossible_select_loc = make_session(impossible_select)
    with pytest.raises(ApplicationError):
        impossible_select_loc.select_row(1)


def test_locator_grid_selection_and_combo_operations():
    component = SimpleNamespace(SelectedRows="", SelectAll=Mock(), ClearSelection=Mock())
    _, loc = make_session(component)
    assert loc.select_all_rows().deselect_all_rows() is loc
    component.SelectAll = None
    del component.ClearSelection
    assert loc.deselect_all_rows() is loc
    impossible_deselect = RejectWrites(rejected=("SelectedRows",))
    _, impossible_deselect_loc = make_session(impossible_deselect)
    with pytest.raises(ApplicationError):
        impossible_deselect_loc.deselect_all_rows()

    component = SimpleNamespace(Key=property)  # direct assignment works on normal stand-in
    component.Key = ""
    _, combo = make_session(component)
    assert combo.select_option("K") is combo and component.Key == "K"

    entry = SimpleNamespace(Key="K1", Value="Invoice")

    class KeyAfterFirstFailure:
        Entries = Collection(entry)

        def __init__(self):
            self.attempts = 0

        @property
        def Key(self):
            return ""

        @Key.setter
        def Key(self, value):
            self.attempts += 1
            if self.attempts == 1:
                raise AttributeError("direct key write unavailable")
            self.key = value

    combo_component = KeyAfterFirstFailure()
    _, combo = make_session(combo_component)
    assert combo.select_option("voice", partial=True) is combo
    assert combo_component.key == "K1"

    value_fallback = RejectWrites(values={"Entries": Collection()}, rejected=("Key", "Value"))
    _, combo = make_session(value_fallback)
    with pytest.raises(ApplicationError):
        combo.select_option("none")

    cell = SimpleNamespace(Text="")
    grid = SimpleNamespace(
        ModifyCell=Mock(side_effect=RuntimeError()),
        Rows=Collection(SimpleNamespace(Item=lambda _: cell)),
    )
    _, grid_loc = make_session(grid)
    assert grid_loc.set_cell_value(0, 1, "x") is grid_loc and cell.Text == "x"
    click_fallback = SimpleNamespace()
    _, click_fallback_loc = make_session(click_fallback)
    assert click_fallback_loc.click_cell(0, 1) is click_fallback_loc

    click_grid = SimpleNamespace(
        SetCurrentCell=Mock(), ClickCurrentCell=Mock(), DoubleClickCurrentCell=Mock()
    )
    _, click_loc = make_session(click_grid)
    assert click_loc.click_cell(1, "C").double_click_cell(1, "C") is click_loc
    bad_grid = RejectWrites(rejected=("SelectedRows",))
    bad_grid.SetCurrentCell = Mock(side_effect=RuntimeError())
    _, bad_loc = make_session(bad_grid)
    with pytest.raises(ApplicationError):
        bad_loc.click_cell(1, 1)
    with pytest.raises(ApplicationError):
        bad_loc.double_click_cell(1, 1)


def test_locator_grid_metadata_sort_scroll_and_context_menu():
    cols = Collection(SimpleNamespace(Title="First", Name="A"), SimpleNamespace(Name="B"))
    comp = SimpleNamespace(ColumnCount=2, Columns=cols, SortAscending=Mock(), SortDescending=Mock())
    session, loc = make_session(comp)
    assert loc.column_count() == 2
    assert loc.column_titles() == ["First", "B"]
    assert loc.sort_by_column("A") is loc and loc.sort_by_column("A", descending=True) is loc

    no_sort = SimpleNamespace(SetCurrentCell=Mock(), ClickCurrentCell=Mock())
    _, no_sort_loc = make_session(no_sort)
    assert no_sort_loc.sort_by_column(1) is no_sort_loc
    impossible = SimpleNamespace(
        SetCurrentCell=Mock(side_effect=RuntimeError()), ClickCurrentCell=Mock()
    )
    _, impossible_loc = make_session(impossible)
    with pytest.raises(ApplicationError):
        impossible_loc.sort_by_column(1)

    scroll = RejectWrites(rejected=("FirstVisibleRow",))
    scroll.VerticalScrollbar = RejectWrites(values={}, rejected=("Position",))
    _, scroll_loc = make_session(scroll)
    with pytest.raises(ApplicationError):
        scroll_loc.scroll_to_row(4)
    first_scroll = SimpleNamespace(FirstVisibleRow=0)
    _, first_loc = make_session(first_scroll)
    assert first_loc.scroll_to_row(4) is first_loc

    col = SimpleNamespace(Title="Vendor", Name="LIFNR")
    table = SimpleNamespace(Columns=Collection(col), GetCellValue=Mock(return_value="100"))
    _, table_loc = make_session(table)
    assert table_loc.cell_value_by_column(0, "vendor") == "100"
    assert table_loc.cell_value_by_column(0, "end", partial=True, case_sensitive=True) == "100"
    with pytest.raises(ElementNotFoundError):
        table_loc.cell_value_by_column(0, "missing")

    context_item = SimpleNamespace(Text="Display", Select=Mock())
    context = SimpleNamespace(Id="ctx", Type="GuiContextMenu", Children=Collection(context_item))
    root = SimpleNamespace(Id="root", Type="GuiMainWindow", Children=Collection(context))
    session._find_now = Mock(return_value=root)
    context_loc = sap.SapLocator(session, id="grid", timeout=0.1)
    context_loc._resolve = Mock(return_value=context)
    context.ShowContextMenu = Mock()
    assert context_loc.right_click().context_menu_click("disp") is context_loc

    no_context = SimpleNamespace()
    _, no_context_loc = make_session(no_context)
    with pytest.raises(ApplicationError):
        no_context_loc.right_click()


def test_locator_text_list_date_and_tree_operations():
    editor = SimpleNamespace(LineCount=3, Text="old")
    _, loc = make_session(editor)
    assert loc.line_count() == 3
    assert loc.append_text("new") is loc and editor.Text == "old\nnew"
    fallback_editor = SimpleNamespace(Text="a\nb")
    _, editor_loc = make_session(fallback_editor)
    assert editor_loc.line_count() == 2
    assert editor_loc.append_text("x", newline=False) is editor_loc
    bad_editor = RejectWrites(values={"Text": ""}, rejected=("Text", "text"))
    _, bad_editor_loc = make_session(bad_editor)
    with pytest.raises(ApplicationError):
        bad_editor_loc.append_text("x")

    items = [
        SimpleNamespace(Key="1", Value="One", Selected=False),
        SimpleNamespace(Key="2", Value="Two", Selected=True),
    ]
    listbox = SimpleNamespace(Entries=Collection(*items), ClearSelection=Mock())
    _, list_loc = make_session(listbox)
    assert list_loc.get_items()[1]["value"] == "Two"
    assert list_loc.select_items(["one"], partial=True) is list_loc and items[0].Selected
    assert list_loc.select_items(["2"], by="key", case_sensitive=True) is list_loc
    bad_list = SimpleNamespace()
    _, bad_list_loc = make_session(bad_list)
    with pytest.raises(ApplicationError):
        bad_list_loc.select_items(["x"])

    calendar = SimpleNamespace(SetSelectionInterval=Mock())
    _, date_loc = make_session(calendar)
    assert date_loc.select_date("2024") is date_loc
    prop_date = RejectWrites(
        rejected=("SetSelectionInterval", "setSelectionInterval", "FocusedDate")
    )
    _, date_loc = make_session(prop_date)
    prop_date.SelectedDate = None
    assert date_loc.select_date("2025") is date_loc
    text_date = RejectWrites(
        rejected=(
            "SetSelectionInterval",
            "setSelectionInterval",
            "FocusedDate",
            "focusedDate",
            "SelectedDate",
            "selectedDate",
        )
    )
    _, date_loc = make_session(text_date)
    assert date_loc.select_date("2026") is date_loc
    impossible_date = RejectWrites(
        rejected=(
            "SetSelectionInterval",
            "setSelectionInterval",
            "FocusedDate",
            "focusedDate",
            "SelectedDate",
            "selectedDate",
            "Text",
            "text",
            "Value",
            "value",
        )
    )
    _, date_loc = make_session(impossible_date)
    with pytest.raises(ApplicationError):
        date_loc.select_date("never")

    tree = SimpleNamespace(
        ExpandNode=Mock(),
        CollapseNode=Mock(),
        SelectNode=Mock(),
        SelectedNode="node",
        GetNodeTextByKey=lambda key: {"1": "Root", "2": "Child"}[key],
        GetSubNodesCol=lambda key: (
            Collection("1") if key == "" else Collection("2") if key == "1" else Collection()
        ),
        Children=Collection(),
    )
    _, tree_loc = make_session(tree)
    assert tree_loc.tree_expand("1").tree_collapse("1").tree_select_node("2") is tree_loc
    assert tree_loc.tree_get_selected() == "node"
    assert tree_loc.tree_find_node("child") == "2"
    assert tree_loc.tree_find_node("Child", partial=True, case_sensitive=True) == "2"
    assert tree_loc.tree_get_all_nodes("1") == ["1", "2"]


def test_locator_tree_combo_and_grid_extraction():
    nodes = Collection("1")
    tree = SimpleNamespace(
        GetSubNodesCol=Mock(side_effect=RuntimeError()),
        Nodes=nodes,
        TopNode="top",
    )
    _, tree_loc = make_session(tree)
    assert tree_loc.tree_get_all_nodes() == ["1"]
    tree.GetSubNodesCol = Mock(return_value=Collection())
    tree.Nodes = Collection()
    assert tree_loc.tree_get_all_nodes() == ["top"]

    combo_entries = Collection(
        SimpleNamespace(Key="A", Value="Alpha"), SimpleNamespace(Key=None, Value=None)
    )
    combo = SimpleNamespace(Entries=combo_entries)
    _, combo_loc = make_session(combo)
    assert combo_loc.get_combo_entries() == [
        {"key": "A", "value": "Alpha"},
        {"key": "", "value": ""},
    ]

    data = {0: {"A": "one", "B": "two"}, 1: {"A": "three", "B": "four"}}
    grid = SimpleNamespace(
        RowCount=2,
        Columns=Collection(SimpleNamespace(Name="A"), SimpleNamespace(Name="B")),
        GetCellValue=lambda row, col: data[row][col],
    )
    _, grid_loc = make_session(grid)
    assert grid_loc.find_row_by_value("A", "THREE") == 1
    assert grid_loc.find_row_by_value("B", "ou", partial=True) == 1
    assert grid_loc.get_all_rows() == [{"A": "one", "B": "two"}, {"A": "three", "B": "four"}]
    assert grid_loc.get_all_rows(["A"]) == [{"A": "one"}, {"A": "three"}]
    with pytest.raises(ElementNotFoundError):
        grid_loc.find_row_by_value("A", "nope")

    no_columns = SimpleNamespace(RowCount=1, ColumnCount=2, GetCellValue=Mock(return_value="v"))
    _, no_columns_loc = make_session(no_columns)
    assert no_columns_loc.get_all_rows() == [{"0": "v", "1": "v"}]


def test_locator_fallbacks_f4_and_wait_combinations():
    session, loc = make_session(SimpleNamespace(SetFocus=Mock()))
    session.send_vkey = Mock(return_value=session)
    assert loc.open_f4_help() is loc
    session.send_vkey.assert_called_with("F4", timeout=0.1)

    no_method = SimpleNamespace()
    _, no_method_loc = make_session(no_method)
    with pytest.raises(ApplicationError):
        no_method_loc.tree_expand("x")
    with pytest.raises(ApplicationError):
        no_method_loc.tree_collapse("x")
    with pytest.raises(ApplicationError):
        no_method_loc.tree_select_node("x")
    assert no_method_loc.tree_get_selected() is None

    direct = SimpleNamespace(SetCurrentCell=Mock(), ClickCurrentCell=Mock())
    _, direct_loc = make_session(direct)
    direct_loc._session.send_vkey = Mock(return_value=direct_loc._session)
    assert direct_loc.click_or_send_vkey("F8") is direct_loc
    direct.SetCurrentCell = None
    direct.ClickCurrentCell = None
    direct_loc.click = Mock(side_effect=ApplicationError("no click"))
    direct_loc._session.send_vkey = Mock(return_value=direct_loc._session)
    assert direct_loc.click_or_send_vkey("F8") is direct_loc

    loc.click = Mock(return_value=loc)
    session.wait_for_popup = Mock(side_effect=WaitTimeoutError("none"))
    assert loc.click_and_confirm() is loc
    session.wait_for_popup = Mock(return_value=session)
    session.dismiss_all_popups = Mock(return_value=session)
    assert loc.click_and_confirm(vkey="F12") is loc
    session.popup_click_button = Mock(return_value=session)
    assert loc.click_and_confirm("Yes") is loc
    assert loc.click_and_dismiss() is loc


def test_remaining_gui_and_connection_defensive_paths(monkeypatch):
    import ctypes

    class ApiCall:
        def __init__(self, result):
            self.result = result

        def __call__(self, *args):
            return self.result(*args) if callable(self.result) else self.result

    class User32:
        GetWindowThreadProcessId = ApiCall(lambda *args: setattr(args[1]._obj, "value", 7))

    class Kernel32:
        OpenProcess = ApiCall(1)
        QueryFullProcessImageNameW = ApiCall(False)
        CloseHandle = ApiCall(True)

    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=User32(), kernel32=Kernel32()))
    assert sap._window_process_name(1) is None

    original_import = builtins.__import__

    def no_pywin32(name, *args, **kwargs):
        if name in {"win32api", "win32con", "win32gui"}:
            raise ImportError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pywin32)
    sap._scripting_security_worker(Mock())

    class FakeThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.started = False

        def start(self):
            self.started = True

    monkeypatch.setattr(sap.threading, "Thread", FakeThread)
    event = sap.SapGui.scripting_security_handler()
    assert isinstance(event, sap.threading.Event)

    client = SimpleNamespace(GetObject=lambda _: SimpleNamespace(GetScriptingEngine="engine"))
    required = Mock(side_effect=[RuntimeError("not ready"), client])
    monkeypatch.setattr(sap, "_require_win32com", required)
    assert sap.SapGui.connect(timeout=1).raw == "engine"

    class BrokenChildren:
        @property
        def Count(self):
            raise RuntimeError("collection gone")

    assert sap.SapGui(SimpleNamespace(Children=BrokenChildren())).connections() == []
    assert sap.SapGui(SimpleNamespace(Children=BrokenChildren())).version == ""

    class CloseChildren:
        Count = 2

        def __call__(self, index):
            if index == 1:
                raise RuntimeError("closed already")
            return SimpleNamespace(CloseConnection=Mock(side_effect=RuntimeError("gone")))

    sap.SapGui(SimpleNamespace(Children=CloseChildren())).close_all_connections()

    gui = sap.SapGui(SimpleNamespace(Children=Collection()))
    gui.connections = Mock(side_effect=[[], [object()]])
    gui.wait_for_any_connection(timeout=1)
    gui.connections = Mock(return_value=[])
    with pytest.raises(WaitTimeoutError):
        gui.wait_for_any_connection(timeout=0)

    class BrokenOpen:
        Children = SimpleNamespace(Count=0)

        def OpenConnection(self, name, sync):
            return SimpleNamespace(Children=Collection())

    assert sap.SapGui(BrokenOpen()).open_connection("QA", timeout=0) is None


def test_remaining_keyboard_connection_and_session_error_paths(monkeypatch):
    original_import = builtins.__import__

    def no_pywinauto(name, *args, **kwargs):
        if name == "pywinauto" or name.startswith("pywinauto."):
            raise ImportError(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pywinauto)
    with pytest.raises(ApplicationError, match="pywinauto not available"):
        sap.SapGui.keyboard_login("SAP", "u", "p")

    class EmptyConnection:
        Children = Collection()

    conn = sap.SapConnection(EmptyConnection())
    with pytest.raises(WaitTimeoutError):
        conn.wait_for_session_count(1, timeout=0)

    class Busy:
        Busy = True

    busy = sap.SapSession(Busy())
    with pytest.raises(WaitTimeoutError, match="still busy"):
        busy.wait_until_ready(timeout=0)

    class NoInfo:
        @property
        def Info(self):
            raise RuntimeError("info unavailable")

    no_info = sap.SapSession(NoInfo())
    assert no_info.current_transaction() == ""
    no_info._find_now = Mock(side_effect=RuntimeError("window gone"))
    assert no_info.title() == ""
    assert no_info.status_message() == ("", "")
    assert no_info.system_info() == {}

    no_vkey = sap.SapSession(SimpleNamespace(Busy=False))
    no_vkey.wait_until_ready = Mock(return_value=no_vkey)
    no_vkey._find_now = Mock(return_value=SimpleNamespace())
    with pytest.raises(ApplicationError, match="SendVKey"):
        no_vkey.send_vkey(0, timeout=0)

    popup = SimpleNamespace(Children=Collection(SimpleNamespace()))
    popup_session = sap.SapSession(SimpleNamespace(Busy=False))
    popup_session.wait_until_ready = Mock(return_value=popup_session)
    popup_session._find_now = Mock(return_value=popup)
    popup_session.send_vkey = Mock(return_value=popup_session)
    popup_session.confirm_popup(timeout=0)
    popup_session.close_popup(timeout=0)
    popup_session.send_vkey.assert_called()

    class NoModalVkey:
        pass

    modal_session = sap.SapSession(SimpleNamespace(Busy=False))
    modal_session.wait_until_ready = Mock(return_value=modal_session)
    modal_session._find_now = Mock(return_value=NoModalVkey())
    modal_session.send_vkey = Mock(return_value=modal_session)
    modal_session.close_popup(timeout=0)

    session = sap.SapSession(SimpleNamespace(Busy=False))
    session.transaction = Mock(side_effect=RuntimeError("closed"))
    session.dismiss_all_popups = Mock(side_effect=RuntimeError("popup gone"))
    session.logoff(confirm=True)


def test_remaining_session_retry_and_selection_paths(monkeypatch):
    session = sap.SapSession(SimpleNamespace(Busy=False))
    session.wait_until_ready = Mock(return_value=session)

    session.transaction = Mock(return_value=session)
    session.execute = Mock(return_value=session)
    session.find_by_id_or_variant = Mock(return_value=SimpleNamespace(set_text=Mock()))
    session.find_by_id = Mock(side_effect=RuntimeError("variant absent"))
    assert session.execute_report("R", variant="V") is session

    session.is_busy = Mock(return_value=False)
    session._find_now = Mock(side_effect=RuntimeError("not found"))
    with pytest.raises(ElementNotFoundError):
        session.find_first_in_tree("GuiButton", timeout=0.001)
    with pytest.raises(ElementNotFoundError):
        session.find_all_in_tree("GuiButton", timeout=0.001)
    session.find_by_id_or_variant = sap.SapSession.find_by_id_or_variant.__get__(session)
    with pytest.raises(ValueError, match="At least one"):
        session.find_by_id_or_variant(timeout=0)
    with pytest.raises(ElementNotFoundError):
        session.find_by_id_or_variant("x", timeout=0.001)

    session._find_now = Mock(side_effect=RuntimeError("popup absent"))
    assert session.dismiss_all_popups(max_attempts=3, timeout=0) is session
    session._find_now = Mock(return_value=SimpleNamespace())
    assert session.dismiss_all_popups(max_attempts=1, timeout=0) is session
    session._find_now = Mock(return_value=SimpleNamespace(SendVKey=Mock()))
    assert session.dismiss_all_popups(vkey="F12", max_attempts=1, timeout=0) is session

    class BadText:
        @property
        def Text(self):
            raise RuntimeError("tab disappeared")

    tab_strip = SimpleNamespace(Type="GuiTabStrip", Children=Collection(BadText()))
    root = SimpleNamespace(Id="root", Type="GuiMainWindow", Children=Collection(tab_strip))
    session._find_now = Mock(return_value=root)
    with pytest.raises(ElementNotFoundError):
        session.click_tab_by_label("missing", timeout=0)

    bad_menu = SimpleNamespace(Children=Collection(BadText()))
    session._find_now = Mock(return_value=bad_menu)
    with pytest.raises(ElementNotFoundError):
        session.menu_click("missing", timeout=0)

    no_select = SimpleNamespace(Text="NoSelect")
    menu = SimpleNamespace(Children=Collection(no_select))
    session._find_now = Mock(return_value=menu)
    with pytest.raises(ElementNotFoundError):
        session.menu_click("NoSelect", timeout=0)
    session._find_now = Mock(side_effect=RuntimeError("menu gone"))
    with pytest.raises(ElementNotFoundError):
        session.menu_click("X", timeout=0)

    session._find_now = Mock(return_value=SimpleNamespace())
    with pytest.raises(ApplicationError, match="Maximize"):
        session.maximize()
    session._find_now = Mock(side_effect=RuntimeError("window gone"))
    assert session.maximize() is session

    session._find_now = Mock(side_effect=RuntimeError("no button"))
    with pytest.raises(ElementNotFoundError):
        session.popup_click_button("Yes", timeout=0)
    with pytest.raises(ElementNotFoundError):
        session.click_toolbar_button("Run", timeout=0)

    session._find_now = Mock(side_effect=RuntimeError("field absent"))
    session.find_by_id = Mock(return_value=SimpleNamespace(set_text=Mock()))
    with pytest.raises(ElementNotFoundError):
        session.set_selection_range("S_X", high="H")
    assert session._selection_field_id("S_X") is None

    session.wait_for_status_message = Mock(return_value=("different", "S"))
    with pytest.raises(AssertionError, match="contain"):
        session.assert_status("wanted", type="S")


def test_remaining_f4_and_session_tree_paths(monkeypatch):
    session = sap.SapSession(SimpleNamespace(Busy=False))
    session.wait_until_ready = Mock(return_value=session)

    with pytest.raises(WaitTimeoutError):
        session.select_f4_value("x", timeout=0)

    empty_popup = SimpleNamespace(Id="p", Type="GuiModalWindow", Children=Collection())
    session._find_now = Mock(return_value=empty_popup)
    with pytest.raises(ElementNotFoundError, match="No grid"):
        session.select_f4_value("x", timeout=0)

    text_field = SimpleNamespace(Id="f", Type="GuiTextField", Text="")
    text_popup = SimpleNamespace(Id="p", Type="GuiModalWindow", Children=Collection(text_field))
    session._find_now = Mock(return_value=text_popup)
    assert session.select_f4_value("abc", timeout=0) is session
    assert text_field.Text == "abc"

    class BrokenColumn:
        @property
        def Name(self):
            raise RuntimeError("no name")

    class TableGrid:
        Id = "grid"
        Type = "GuiTableControl"
        Rows = Collection("r0", "r1")
        Columns = Collection(BrokenColumn())
        RowCount = None

        def GetCellValue(self, row, column):
            if row == 0:
                raise RuntimeError("not loaded")
            return "hit"

        def SetCurrentCell(self, row, column):
            raise RuntimeError("no current cell")

        DoubleClickCurrentCell = Mock()

    grid = TableGrid()
    f4_popup = SimpleNamespace(Id="p", Type="GuiModalWindow", Children=Collection(grid))
    session._find_now = Mock(return_value=f4_popup)
    session.dismiss_all_popups = Mock(return_value=session)
    assert session.select_f4_value("hit", timeout=0) is session
    assert grid.DoubleClickCurrentCell.called

    session._find_now = Mock(return_value=f4_popup)
    with pytest.raises(ElementNotFoundError):
        session.select_f4_value("missing", column=0, timeout=0)

    lower = sap.SapSession(SimpleNamespace(findById=lambda key: "found"))
    assert lower._find_now("x") == "found"
    with pytest.raises(AttributeError, match="does not expose"):
        sap.SapSession(SimpleNamespace())._find_now("x")

    found = object()
    wait_session = sap.SapSession(SimpleNamespace(Busy=False))
    wait_session._find_component = Mock(return_value=found)
    assert wait_session._wait_component(id="x", name=None, type=None, timeout=0) is found
    no_component = sap.SapSession(SimpleNamespace(Busy=False))
    assert no_component._find_component(id=None, name=None, type=None) is None
    child = SimpleNamespace(Id="child", Type="GuiButton", Name="go", Children=Collection())
    root = SimpleNamespace(Id="root", Type="GuiWindow", Children=Collection(child))
    tree_session = sap.SapSession(SimpleNamespace(Busy=False, Children=Collection(child)))
    tree_session._find_now = Mock(side_effect=lambda key: child if key == "child" else root)
    assert tree_session._find_component(id="child", name="go", type="GuiButton") is child
    assert tree_session._find_component(id="child", name="other", type="GuiButton") is None
    assert tree_session._find_component(id=None, name="go", type="GuiButton") is child
    tree_session._find_now = Mock(side_effect=RuntimeError("no root"))
    assert tree_session._find_component(id=None, name="no", type="GuiButton") is None
    assert (
        sap.SapSession._selector(id="x", name="n", type="GuiButton")
        == "{id='x', name='n', type='GuiButton'}"
    )


def test_remaining_locator_boolean_and_cell_fallbacks():
    class Flags:
        ReadOnly = False
        Enabled = False
        Changeable = True

    _, loc = make_session(Flags())
    assert not loc.is_enabled()

    class ChangeFlags:
        ReadOnly = False
        Enabled = True
        Changeable = False

    _, loc = make_session(ChangeFlags())
    assert not loc.is_enabled()
    _, loc = make_session(RejectWrites(rejected=("Selected", "selected")))
    assert not loc.is_checked()
    with pytest.raises(ApplicationError):
        loc.set_checked(True)
    no_rows = SimpleNamespace()
    _, no_rows_loc = make_session(no_rows)
    assert no_rows_loc.row_count() == 0

    normal = SimpleNamespace()
    _, normal_loc = make_session(normal)
    assert normal_loc.select_row(1) is normal_loc

    class ValueFallback:
        Entries = Collection()

        def __setattr__(self, name, value):
            if name == "Key":
                raise AttributeError(name)
            object.__setattr__(self, name, value)

    combo = ValueFallback()
    _, combo_loc = make_session(combo)
    assert combo_loc.select_option("Value") is combo_loc
    assert combo.Value == "Value"

    class BadEntryCollection(Collection):
        def __call__(self, index):
            raise RuntimeError("entry vanished")

    bad_entries = RejectWrites(
        values={"Entries": BadEntryCollection("x")}, rejected=("Key", "Value")
    )
    _, bad_combo = make_session(bad_entries)
    with pytest.raises(ApplicationError):
        bad_combo.select_option("x")

    bad_write = SimpleNamespace(
        ModifyCell=Mock(side_effect=RuntimeError()),
        Rows=Collection(SimpleNamespace(Item=lambda col: (_ for _ in ()).throw(RuntimeError()))),
    )
    _, bad_write_loc = make_session(bad_write)
    with pytest.raises(ApplicationError):
        bad_write_loc.set_cell_value(0, 0, "x")

    no_count = SimpleNamespace(Columns=Collection(SimpleNamespace(Name="A")))
    _, no_count_loc = make_session(no_count)
    assert no_count_loc.column_count() == 1
    _, broken_columns_loc = make_session(SimpleNamespace(Columns=object()))
    assert broken_columns_loc.column_count() == 0

    titles = SimpleNamespace(Columns=Collection(SimpleNamespace(Title="")), ColumnCount=1)
    _, titles_loc = make_session(titles)
    assert titles_loc.column_titles() == [""]
    _, no_titles_loc = make_session(SimpleNamespace(ColumnCount=2))
    assert no_titles_loc.column_titles() == []
    with pytest.raises(ApplicationError):
        make_session(SimpleNamespace())[1].select_all_rows()


def test_remaining_dialog_collection_and_backend_paths(monkeypatch):
    class BadClass:
        def IsWindowVisible(self, hwnd):
            return True

        def GetClassName(self, hwnd):
            raise RuntimeError("window disappeared")

    assert sap._is_scripting_security_dialog(1, BadClass()) is False

    client = SimpleNamespace()
    package = SimpleNamespace(client=client)
    monkeypatch.setitem(sys.modules, "win32com", package)
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    assert sap._require_win32com() is client

    monkeypatch.setattr("dolphin_desktop._backend.resolve", lambda backend_id: backend_id)
    assert sap.SapGui.backend() == "sap"

    class BadItems(Collection):
        def __call__(self, index):
            if index == 0:
                raise RuntimeError("item disappeared")
            return super().__call__(index)

    parent = SimpleNamespace(Children=BadItems("bad", "good"))
    assert list(sap._iter_children(parent)) == ["good"]
    assert sap._parse_selected_rows("8-5") == [5, 6, 7, 8]


def test_remaining_session_popup_tab_menu_and_wait_timeout_paths():
    session = sap.SapSession(SimpleNamespace(Busy=False))
    session.wait_until_ready = Mock(return_value=session)
    session.is_busy = Mock(return_value=False)

    session._find_now = Mock(side_effect=RuntimeError("screen gone"))
    with pytest.raises(ElementNotFoundError):
        session.click_tab_by_label("x", timeout=0.001)
    with pytest.raises(ElementNotFoundError):
        session.menu_click("x", timeout=0.001)
    with pytest.raises(WaitTimeoutError):
        session.wait_for_popup(timeout=0.001)

    session.is_busy = Mock(side_effect=RuntimeError("busy probe failed"))
    with pytest.raises(ElementNotFoundError):
        session.popup_click_button("x", timeout=0.001)

    session.is_busy = Mock(return_value=False)
    with pytest.raises(ElementNotFoundError):
        session.click_toolbar_button("x", timeout=0.001)

    session._find_now = Mock(return_value=SimpleNamespace())
    with pytest.raises(ElementNotFoundError):
        session.menu_click("x", timeout=0)

    class BadTabText:
        @property
        def Text(self):
            raise RuntimeError("tab gone")

    tab_strip = SimpleNamespace(Type="GuiTabStrip", Children=Collection(BadTabText()))
    root = SimpleNamespace(Id="root", Type="GuiWindow", Children=Collection(tab_strip))
    session._find_now = Mock(return_value=root)
    with pytest.raises(ElementNotFoundError):
        session.click_tab_by_label("x", timeout=0)


def test_remaining_locator_metadata_and_collection_error_paths():
    class BadColumnCollection:
        Count = 2

        def __call__(self, index):
            if index == 0:
                raise RuntimeError("column gone")
            return SimpleNamespace(Name="B", Title="Bee")

    class RejectFirstVisible:
        RowCount = 2
        Columns = BadColumnCollection()

        def __setattr__(self, name, value):
            if name == "FirstVisibleRow":
                raise RuntimeError("viewport locked")
            object.__setattr__(self, name, value)

        def GetCellValue(self, row, column):
            if column in ("B", 1):
                raise RuntimeError("cell not loaded")
            return "ok"

    grid = RejectFirstVisible()
    _, loc = make_session(grid)
    assert loc.column_titles() == ["Bee"]
    assert loc.get_all_rows() == [{"0": "ok", "B": ""}, {"0": "ok", "B": ""}]
    assert loc.find_row_by_value("A", "ok") == 0

    class BrokenColumns:
        @property
        def Columns(self):
            raise RuntimeError("columns unavailable")

    _, broken = make_session(BrokenColumns())
    assert broken.column_count() == 0
    assert broken.column_titles() == []

    class VerticalOnly:
        def __setattr__(self, name, value):
            if name == "FirstVisibleRow":
                raise RuntimeError(name)
            object.__setattr__(self, name, value)

    vertical = VerticalOnly()
    vertical.VerticalScrollbar = SimpleNamespace(Position=0)
    _, vertical_loc = make_session(vertical)
    assert vertical_loc.scroll_to_row(5) is vertical_loc

    class NamedColumnFailure:
        Title = "Title"

        @property
        def Name(self):
            raise RuntimeError("name unavailable")

    table = SimpleNamespace(
        Columns=Collection(NamedColumnFailure()), GetCellValue=Mock(return_value="x")
    )
    _, table_loc = make_session(table)
    assert table_loc.cell_value_by_column(0, "Title") == "x"


def test_remaining_context_text_list_and_selection_fallbacks():
    session = sap.SapSession(SimpleNamespace(Busy=False))
    session.wait_until_ready = Mock(return_value=session)

    item = SimpleNamespace(Text="Option")
    popup = SimpleNamespace(Id="p", Type="GuiWindow", Children=Collection(item))
    session._find_now = Mock(side_effect=lambda key: popup if key == "wnd[1]" else object())
    context_loc = sap.SapLocator(session, id="grid", timeout=0.1)
    context_loc._resolve = Mock(return_value=popup)
    with pytest.raises(ElementNotFoundError):
        context_loc.context_menu_click("missing")
    with pytest.raises(ApplicationError, match="cannot be activated"):
        context_loc.context_menu_click("Option", partial=False)

    class BrokenMenuItem:
        Text = "Option"

    broken_popup = SimpleNamespace(
        Id="p", Type="GuiContextMenu", Children=Collection(BrokenMenuItem())
    )
    session._find_now = Mock(side_effect=lambda key: broken_popup if key == "wnd[1]" else object())
    with pytest.raises(ApplicationError, match="cannot be activated"):
        context_loc.context_menu_click("Option", partial=False)

    class NoText:
        @property
        def Text(self):
            raise RuntimeError("text unavailable")

    _, text_loc = make_session(NoText())
    assert text_loc.line_count() == 0

    class BadItems:
        Count = 2

        def __call__(self, index):
            if index == 0:
                raise RuntimeError("list item gone")
            return SimpleNamespace(Key="2", Value="Two", Selected=False)

    list_component = SimpleNamespace(Entries=object(), Items=BadItems())
    _, list_loc = make_session(list_component)
    assert list_loc.get_items() == [{"key": "2", "value": "Two", "selected": "False"}]

    class NoListCollections:
        @property
        def Entries(self):
            raise RuntimeError("entries absent")

        @property
        def Items(self):
            raise RuntimeError("items absent")

        @property
        def Children(self):
            raise RuntimeError("children absent")

    _, no_list = make_session(NoListCollections())
    assert no_list.get_items() == []

    selected = SimpleNamespace(Key="1", Value="One", Selected=False)

    class BadSelectable:
        Count = 2

        def __call__(self, index):
            if index == 0:
                raise RuntimeError("selectable disappeared")
            return selected

    selectable = SimpleNamespace(Entries=object(), Items=BadSelectable())
    _, selectable_loc = make_session(selectable)
    assert selectable_loc.select_items(["One"]) is selectable_loc

    class NoSelectable:
        @property
        def Entries(self):
            raise RuntimeError("entries absent")

        @property
        def Items(self):
            raise RuntimeError("items absent")

        @property
        def Children(self):
            raise RuntimeError("children absent")

    _, no_selectable = make_session(NoSelectable())
    with pytest.raises(ApplicationError):
        no_selectable.select_items(["x"])


def test_remaining_tree_walk_and_tree_locator_fallback_paths():
    leaf = SimpleNamespace(Id="leaf", Type="GuiButton", Text="Target", Children=Collection())
    assert sap._find_in_tree(leaf, name=None, type="GuiButton", seen=set(), depth=-1) is None
    assert sap._find_in_tree(leaf, name=None, type="GuiButton", seen={"leaf"}, depth=1) is None
    all_found = []
    sap._find_all_in_tree(leaf, type="GuiButton", seen=set(), depth=-1, result=all_found)
    assert all_found == []
    assert (
        sap._find_by_text(
            leaf,
            text="missing",
            comp_type=None,
            partial=False,
            case_sensitive=False,
            seen=set(),
            depth=0,
        )
        is None
    )
    assert (
        sap._find_by_text(
            leaf,
            text="Target",
            comp_type=None,
            partial=False,
            case_sensitive=False,
            seen={"leaf"},
            depth=1,
        )
        is None
    )
    assert (
        sap._find_by_text(
            leaf,
            text="missing",
            comp_type=None,
            partial=False,
            case_sensitive=False,
            seen=set(),
            depth=-1,
        )
        is None
    )

    class FallbackTree:
        def GetSubNodesCol(self, key):
            raise RuntimeError("subnodes unavailable")

        Nodes = Collection("top")

        def GetNodeTextByKey(self, key):
            raise RuntimeError("new API unavailable")

        def GetItemText(self, key, column):
            return "Target"

    _, tree_loc = make_session(FallbackTree())
    assert tree_loc.tree_find_node("target", partial=False) == "top"

    class LastResortTree:
        def GetSubNodesCol(self, key):
            raise RuntimeError("subnodes unavailable")

        @property
        def Nodes(self):
            raise RuntimeError("nodes unavailable")

        TopNode = "top"

        def GetNodeTextByKey(self, key):
            return "Target"

    _, tree_loc = make_session(LastResortTree())
    assert tree_loc.tree_find_node("Target", partial=False) == "top"

    class NoMatchTree:
        TopNode = ""

        def GetSubNodesCol(self, key):
            return Collection("top") if key == "" else Collection()

        def GetNodeTextByKey(self, key):
            return "Other"

    _, tree_loc = make_session(NoMatchTree())
    with pytest.raises(ElementNotFoundError):
        tree_loc.tree_find_node("missing", partial=False)

    class BadNodeCollection(Collection):
        def __call__(self, index):
            if index == 0:
                raise RuntimeError("node disappeared")
            return "second"

    class NodeListFallback:
        def GetSubNodesCol(self, key):
            if key == "":
                raise RuntimeError("root collection unavailable")
            return Collection()

        Nodes = BadNodeCollection("first", "second")

    _, tree_loc = make_session(NodeListFallback())
    assert tree_loc.tree_get_all_nodes() == ["second"]

    class BrokenNodeList:
        def GetSubNodesCol(self, key):
            raise RuntimeError("root collection unavailable")

        @property
        def Nodes(self):
            raise RuntimeError("nodes unavailable")

        TopNode = "last"

    _, tree_loc = make_session(BrokenNodeList())
    assert tree_loc.tree_get_all_nodes() == ["last"]

    class DeepNode:
        def __init__(self, number):
            self.Id = f"node-{number}"
            self.Type = "GuiContainer"
            self.Children = Collection()

    chain = [DeepNode(i) for i in range(33)]
    for parent, child in itertools.pairwise(chain):
        parent.Children = Collection(child)
    chain[-1].Children = Collection(chain[0])
    enumerate_session = sap.SapSession(SimpleNamespace(Busy=False))
    enumerate_session._find_now = Mock(return_value=chain[0])
    entries = enumerate_session.enumerate_screen_types()
    assert len(entries) == 31


def test_remaining_worker_collection_and_wait_exception_paths(monkeypatch):
    class Event:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 1

    class Gui:
        def __init__(self, child_error=False, enum_error=False):
            self.child_error = child_error
            self.enum_error = enum_error

        def EnumWindows(self, callback, extra):
            if self.enum_error:
                raise RuntimeError("desktop changed")
            callback(1, extra)

        def EnumChildWindows(self, hwnd, callback, extra):
            if self.child_error:
                raise RuntimeError("dialog disappeared")
            callback(2, extra)

        def GetWindowText(self, hwnd):
            return "&Allow"

    api = SimpleNamespace(SendMessage=Mock(side_effect=RuntimeError("click failed")))
    con = SimpleNamespace(BM_CLICK=1)
    for gui in (Gui(), Gui(child_error=True), Gui(enum_error=True)):
        monkeypatch.setitem(sys.modules, "win32api", api)
        monkeypatch.setitem(sys.modules, "win32con", con)
        monkeypatch.setitem(sys.modules, "win32gui", gui)
        monkeypatch.setattr(sap, "_is_scripting_security_dialog", lambda hwnd, _: False)
        sap._scripting_security_worker(Event())

    monkeypatch.setattr(sap, "_is_scripting_security_dialog", lambda hwnd, _: True)
    for gui in (Gui(child_error=True), Gui(enum_error=True), Gui()):
        monkeypatch.setitem(sys.modules, "win32gui", gui)
        sap._scripting_security_worker(Event())
    api.SendMessage.side_effect = None

    class BrokenChildren:
        @property
        def Count(self):
            raise RuntimeError("children unavailable")

    sap.SapGui(SimpleNamespace(Children=BrokenChildren())).close_all_connections()

    class ScanChildren:
        Count = 1

        def __call__(self, index):
            return SimpleNamespace(Children=object())

    scanned = SimpleNamespace(
        Children=ScanChildren(),
        OpenConnection=Mock(return_value=SimpleNamespace(Children=Collection())),
    )
    assert sap.SapGui(scanned).open_connection("QA", timeout=0) is None

    class CountFailure:
        @property
        def Count(self):
            raise RuntimeError("count unavailable")

    failed_scan = SimpleNamespace(
        Children=CountFailure(),
        OpenConnection=Mock(return_value=SimpleNamespace(Children=Collection())),
    )
    assert sap.SapGui(failed_scan).open_connection("QA", timeout=0) is None

    with pytest.raises(WaitTimeoutError):
        sap.SapConnection(SimpleNamespace(Children=Collection())).wait_for_session_count(
            1, timeout=0.001
        )
    with pytest.raises(WaitTimeoutError):
        sap.SapSession(SimpleNamespace(Busy=True)).wait_until_ready(timeout=0.001)
    title_session = sap.SapSession(SimpleNamespace(Busy=False))
    title_session.title = Mock(return_value="other")
    with pytest.raises(WaitTimeoutError):
        title_session.wait_for_title("wanted", timeout=0.001)

    class BusyFailure:
        @property
        def Busy(self):
            raise RuntimeError("busy probe failed")

    assert sap.SapSession(BusyFailure()).is_busy() is False


def test_remaining_locator_and_tree_edge_branches():
    session = sap.SapSession(SimpleNamespace(Busy=False))
    session.wait_until_ready = Mock(return_value=session)
    session.is_busy = Mock(return_value=False)
    with pytest.raises(WaitTimeoutError):
        sap.SapLocator(session, id="x", timeout=0.001).wait_for(state="visible")

    duplicate = SimpleNamespace(Id="same", Type="GuiWindow", Children=Collection())
    duplicate.Children = Collection(duplicate)
    session._find_now = Mock(return_value=duplicate)
    assert len(session.enumerate_screen_types()) == 1

    class BrokenEntries:
        Key = ""

        @property
        def Entries(self):
            raise RuntimeError("entries unavailable")

        def __setattr__(self, name, value):
            if name in {"Key", "Value"}:
                raise AttributeError(name)
            object.__setattr__(self, name, value)

    _, broken_combo = make_session(BrokenEntries())
    with pytest.raises(ApplicationError):
        broken_combo.select_option("x")

    empty_columns = SimpleNamespace(Columns=Collection(), ColumnCount=2)
    _, empty_columns_loc = make_session(empty_columns)
    assert empty_columns_loc.column_titles() == []

    class FailingTree:
        TopNode = "top"

        def GetSubNodesCol(self, key):
            if key == "":
                return Collection("top")
            raise RuntimeError("children unavailable")

        def GetNodeTextByKey(self, key):
            if key == "top":
                raise RuntimeError("label API unavailable")
            return "other"

        def GetItemText(self, key, column):
            raise RuntimeError("fallback label unavailable")

    _, failing_tree = make_session(FailingTree())
    with pytest.raises(ElementNotFoundError):
        failing_tree.tree_find_node("missing", partial=False)

    class BadTopItems(Collection):
        def __call__(self, index):
            raise RuntimeError("top item gone")

    class BadTreeList:
        TopNode = "last"
        GetSubNodesCol = Mock(side_effect=RuntimeError("root unavailable"))
        Nodes = BadTopItems("one")

    _, bad_tree_list = make_session(BadTreeList())
    assert bad_tree_list.tree_get_all_nodes() == ["last"]

    class Vertical:
        def __setattr__(self, name, value):
            if name == "FirstVisibleRow":
                raise RuntimeError(name)
            object.__setattr__(self, name, value)

    vertical = Vertical()
    vertical.VerticalScrollbar = SimpleNamespace(Position=0)
    _, vertical_loc = make_session(vertical)
    assert vertical_loc.scroll_to_row(2) is vertical_loc

    context = sap.SapLocator(session, id="x", timeout=0.1)
    context._resolve = Mock(return_value=object())
    session._find_now = Mock(side_effect=RuntimeError("menu gone"))
    with pytest.raises(ElementNotFoundError):
        context.context_menu_click("x")


def test_remaining_unusual_com_collection_fallbacks():
    modified = SimpleNamespace(ModifyCell=Mock())
    _, modified_loc = make_session(modified)
    assert modified_loc.set_cell_value(0, "A", "value") is modified_loc
    modified.ModifyCell.assert_called_once_with(0, "A", "value")

    class BadTop(Collection):
        def __call__(self, index):
            raise RuntimeError("top node vanished")

    class BadNodeTree:
        def GetSubNodesCol(self, key):
            if key == "":
                return BadTop("x")
            return Collection()

        TopNode = "top"

        def GetNodeTextByKey(self, key):
            return "other"

    _, bad_top_loc = make_session(BadNodeTree())
    with pytest.raises(ElementNotFoundError):
        bad_top_loc.tree_find_node("missing", partial=False)

    class BadNodesTree:
        def GetSubNodesCol(self, key):
            raise RuntimeError("top collection unavailable")

        @property
        def Nodes(self):
            return BadTop("x")

        TopNode = "top"

        def GetNodeTextByKey(self, key):
            return "other"

    _, bad_nodes_loc = make_session(BadNodesTree())
    with pytest.raises(ElementNotFoundError):
        bad_nodes_loc.tree_find_node("missing", partial=False)

    class NoTopTree:
        def GetSubNodesCol(self, key):
            raise RuntimeError("top collection unavailable")

        @property
        def Nodes(self):
            raise RuntimeError("nodes unavailable")

        @property
        def TopNode(self):
            raise RuntimeError("top node unavailable")

    _, no_top_loc = make_session(NoTopTree())
    with pytest.raises(ElementNotFoundError):
        no_top_loc.tree_find_node("missing", partial=False)

    class BadTopList:
        def GetSubNodesCol(self, key):
            if key == "":
                return BadTop("x")
            return Collection()

        @property
        def TopNode(self):
            raise RuntimeError("top node unavailable")

    _, bad_top_list_loc = make_session(BadTopList())
    assert bad_top_list_loc.tree_get_all_nodes() == []

    class BadComboItems(Collection):
        def __call__(self, index):
            raise RuntimeError("combo entry vanished")

    _, combo_items_loc = make_session(SimpleNamespace(Entries=BadComboItems("x")))
    assert combo_items_loc.get_combo_entries() == []

    class BrokenCombo:
        @property
        def Entries(self):
            raise RuntimeError("combo entries unavailable")

    _, broken_combo_loc = make_session(BrokenCombo())
    assert broken_combo_loc.get_combo_entries() == []

    broken_grid = SimpleNamespace(
        RowCount=1, GetCellValue=Mock(side_effect=RuntimeError("cell gone"))
    )
    _, broken_grid_loc = make_session(broken_grid)
    with pytest.raises(ElementNotFoundError):
        broken_grid_loc.find_row_by_value("A", "x")

    class BadColumnItems(Collection):
        def __call__(self, index):
            raise RuntimeError("column vanished")

    _, bad_column_loc = make_session(SimpleNamespace(Columns=BadColumnItems("x")))
    with pytest.raises(ElementNotFoundError):
        bad_column_loc.cell_value_by_column(0, "x")

    class BrokenColumnOwner:
        @property
        def Columns(self):
            raise RuntimeError("columns unavailable")

    _, broken_column_loc = make_session(BrokenColumnOwner())
    with pytest.raises(ElementNotFoundError):
        broken_column_loc.cell_value_by_column(0, "x")


def test_sap_type_matching_supports_shell_subtypes_and_cycle_guards() -> None:
    from dolphin_desktop._sap import _key_seen_before, _matches

    shell = SimpleNamespace(Name="grid", Type="GuiShell", SubType="GridView")
    splitter = SimpleNamespace(Type="GuiSplitterShell")
    assert _matches(shell, name="grid", type="GuiGridView") is True
    assert _matches(splitter, name=None, type="GuiSplitter") is True
    seen: set[str] = set()
    assert _key_seen_before("wnd[0]/usr", seen) is False
    assert _key_seen_before("wnd[0]/usr", seen) is True
    assert _key_seen_before(None, seen) is False


def test_sap_collection_and_virtual_key_helpers_preserve_com_semantics() -> None:
    class _Children:
        Count = 2

        def __call__(self, index: int) -> str:
            return ["first", "second"][index]

    class _Parent:
        Children = _Children()

    assert _collection_count(_Children()) == 2
    assert list(_iter_children(_Parent())) == ["first", "second"]
    called: list[str] = []

    class _Target:
        def press(self, key: str) -> None:
            called.append(key)

    assert _call_first(_Target(), ("missing", "press"), "ENTER") is True
    assert called == ["ENTER"]
    assert _normalize_vkey(" enter ") == 0
    assert _normalize_vkey("15") == 15
    with pytest.raises(ValueError, match="Unsupported SAP virtual key"):
        _normalize_vkey("not-a-key")


class _TreeCollection:
    def __init__(self, *items):
        self.items = list(items)
        self.Count = len(self.items)

    def __call__(self, index):
        return self.items[index]


def test_sap_tree_search_helpers_match_aliases_text_and_cycles() -> None:
    import dolphin_desktop._sap as sap

    leaf = SimpleNamespace(Id="leaf", Type="GuiButton", Text="Save", Name="save")
    shell = SimpleNamespace(
        Id="shell", Type="GuiShell", SubType="GridView", Children=_TreeCollection(leaf)
    )
    root = SimpleNamespace(Id="root", Type="GuiMainWindow", Children=_TreeCollection(shell))
    assert sap._matches(leaf, name="save", type="GuiButton")
    assert sap._matches(shell, name=None, type="GuiGridView")
    assert sap._matches(SimpleNamespace(Type="GuiSplitterShell"), name=None, type="GuiSplitter")
    assert sap._find_in_tree(root, name="save", type="GuiButton", seen=set(), depth=4) is leaf
    result = []
    sap._find_all_in_tree(root, type="GuiButton", seen=set(), depth=4, result=result)
    assert result == [leaf]
    assert (
        sap._find_by_text(
            root,
            text="sav",
            comp_type="GuiButton",
            partial=True,
            case_sensitive=False,
            seen=set(),
            depth=4,
        )
        is leaf
    )
    assert sap._component_key(leaf) == "leaf"
    assert sap._key_seen_before("leaf", {"leaf"}) is True


class _SessionCollection:
    def __init__(self, *items):
        self.items = list(items)
        self.Count = len(self.items)

    def __call__(self, index):
        return self.items[index]


def test_sap_session_tree_queries_and_locator_table_fallbacks() -> None:
    import dolphin_desktop._sap as sap

    leaf = SimpleNamespace(Id="leaf", Type="GuiButton", Text="Save", Name="save")
    root = SimpleNamespace(Id="root", Type="GuiMainWindow", Children=_SessionCollection(leaf))
    session = sap.SapSession(SimpleNamespace(Busy=False))
    session._find_now = Mock(return_value=root)
    first = session.find_first_in_tree("GuiButton", timeout=0.1)
    assert first._id == "leaf"
    all_items = session.find_all_in_tree("GuiButton", timeout=0.1)
    assert [item._id for item in all_items] == ["leaf"]

    rows = _SessionCollection(
        SimpleNamespace(Item=lambda column: SimpleNamespace(Text=f"r0c{column}"))
    )
    table = SimpleNamespace(RowCount=None, Rows=rows)
    locator = sap.SapLocator(session, id="table", timeout=0.1)
    locator._resolve = Mock(return_value=table)
    assert locator.row_count() == 1
    assert locator.cell_value(0, 2) == "r0c2"


def test_sap_com_helpers_try_supported_shapes_without_hiding_call_errors() -> None:
    from dolphin_desktop import _sap

    class Collection:
        Count = 2

        def __call__(self, index):
            raise TypeError("not callable")

        def Item(self, index):
            return ("a", "b")[index]

    assert _sap._collection_item(Collection(), 1) == "b"
    assert _sap._collection_count(Collection()) == 2
    assert _sap._iter_children(SimpleNamespace(Children=Collection())) == ["a", "b"]
    assert _sap._children(object()) is None

    class Setter:
        first = None
        second = "value"

        def __setattr__(self, name, value):
            if name == "missing":
                raise AttributeError(name)
            object.__setattr__(self, name, value)

    target = Setter()
    assert _sap._get_first(target, ("first", "second")) == "value"
    assert _sap._set_first(target, ("missing", "second"), "new") is True
    assert target.second == "new"
    assert _sap._call_first(SimpleNamespace(), ("missing",)) is False
    failing = SimpleNamespace(run=Mock(side_effect=RuntimeError("SAP failed")))
    with pytest.raises(RuntimeError, match="SAP failed"):
        _sap._call_first(failing, ("run",))

    class LazyEngine:
        @property
        def Children(self):
            raise RuntimeError("not connected")

        def __call__(self):
            return "created"

    engine = SimpleNamespace(GetScriptingEngine=LazyEngine())
    assert _sap._get_scripting_engine(engine) == "created"
    assert _sap._bool_attr(SimpleNamespace(flag=1), ("flag",), False) is True
    assert _sap._bool_attr(object(), ("flag",), True) is True


class _GuiCollection:
    def __init__(self, *items):
        self._items = list(items)
        self.Count = len(self._items)

    def __call__(self, index):
        return self._items[index]


def test_sap_gui_and_connection_facades_cover_com_collections(monkeypatch) -> None:
    import dolphin_desktop._sap as sap

    session_com = SimpleNamespace(Busy=False, Info=SimpleNamespace(Transaction="SE16"))
    connection_com = SimpleNamespace(
        Description="QA system",
        Children=_GuiCollection(session_com),
        CloseConnection=Mock(),
    )
    raw = SimpleNamespace(
        Children=_GuiCollection(connection_com),
        Version="7700.10",
        OpenConnection=Mock(return_value=connection_com),
    )
    gui = sap.SapGui(raw)
    assert gui.raw is raw
    assert gui.version == "7700.10"
    assert len(gui.connections()) == 1
    assert gui.connection().description == "QA system"
    assert len(gui.connection().sessions()) == 1
    gui.connection().close()
    connection_com.CloseConnection.assert_called_once_with()
    gui.close_all_connections()
    assert connection_com.CloseConnection.call_count == 2
    assert gui.session().raw is session_com
    assert gui.open_connection("QA", timeout=0.1).raw is session_com

    session_com.CreateSession = Mock()
    gui.connection().create_session()
    session_com.CreateSession.assert_called_once_with()
    gui.connection().wait_for_session_count(1, timeout=0.1)
    with pytest.raises(ValueError, match="at least one"):
        sap.SapSession(session_com).locator()

    monkeypatch.setattr(
        sap,
        "_require_win32com",
        lambda: SimpleNamespace(
            GetObject=lambda _: SimpleNamespace(
                GetScriptingEngine=SimpleNamespace(Children=raw.Children)
            )
        ),
    )
    assert sap.SapGui.connect(timeout=0.1).raw.Children is raw.Children

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr("socket.create_connection", lambda address, timeout: Socket())
    assert sap.SapGui.is_server_reachable("server", 3200, timeout=0.1)
    monkeypatch.setattr("socket.create_connection", Mock(side_effect=OSError("offline")))
    assert not sap.SapGui.is_server_reachable("server", 3200, timeout=0.1)


def test_sap_session_and_locator_cover_actions_state_and_grid_access(monkeypatch) -> None:
    import dolphin_desktop._sap as sap

    window = SimpleNamespace(
        Text="SAP Easy Access",
        Press=Mock(),
        DoubleClick=Mock(),
        SetFocus=Mock(),
        Selected=False,
        Required=True,
        ReadOnly=False,
        Enabled=True,
        Changeable=True,
        Tooltip="tip",
        RowCount=3,
        GetCellValue=Mock(return_value="cell"),
        SelectRow=Mock(),
    )
    session = sap.SapSession(SimpleNamespace(Busy=False))
    session._wait_component = Mock(return_value=window)
    session.wait_until_ready = Mock(return_value=session)
    session.send_vkey = Mock(return_value=session)
    session._find_now = Mock(return_value=window)
    locator = session.find_by_id("wnd[0]/usr/grid", timeout=0.1)

    assert locator.raw is window
    assert locator.click().double_click().set_text("new").type_text("typed").clear() is locator
    assert window.Text == ""
    assert locator.focus().press_key("F8") is locator
    assert window.SetFocus.call_count == 2
    window.Text = "SAP Easy Access"
    assert locator.text() == "SAP Easy Access"
    assert locator.value() == "SAP Easy Access"
    assert locator.exists(timeout=0.1)
    assert locator.is_visible()
    assert locator.is_enabled()
    assert locator.is_checked() is False
    assert locator.set_checked(True).is_checked() is True
    assert locator.get_attribute("text") == "SAP Easy Access"
    with pytest.raises(AttributeError, match="no attribute"):
        locator.get_attribute("missing")
    assert locator.get_attribute("missing", "fallback") == "fallback"
    assert locator.wait_for(state="exists", timeout=0.1) is locator
    assert locator.wait_until_enabled(0.1) is locator
    assert locator.is_mandatory()
    assert not locator.is_readonly()
    assert locator.get_tooltip() == "tip"
    assert locator.row_count() == 3
    assert locator.cell_value(1, "NAME") == "cell"
    assert locator.select_row(2) is locator
    assert locator.timeout(0.2)._timeout == 0.2
    assert repr(locator).startswith("SapLocator(")

    window.Press.side_effect = RuntimeError("not clickable")
    session.send_vkey.reset_mock()
    assert locator.click_or_send_vkey("F8") is locator
    session.send_vkey.assert_called_once_with("F8")
    window.Press.side_effect = None
    monkeypatch.setattr(session, "wait_for_popup", Mock(side_effect=sap.WaitTimeoutError("none")))
    assert locator.click_and_confirm(timeout=0.1) is locator
