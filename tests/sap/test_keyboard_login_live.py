"""SAP GUI 800 validation of ``SapGui.keyboard_login()`` (KAN-577, docs/guides/sap.md).

The mock unit tests in ``tests/framework/test_sap.py`` pin the UIA contract;
this module runs the documented release procedure against a real logon screen:

1. inventory the UIA shape of the logon screen (ids, password classification,
   writable Value patterns) without reading any field value;
2. log in with runtime credentials;
3. type SendKeys metacharacters literally;
4. abort when focus starts on Client/Language or moves during the login;
5. when the client exposes no usable UIA login fields, refuse safely: no field
   changed, nothing submitted.

Everything reads the screen through SAP GUI Scripting, which is independent
of the UIA path under test. The password is never read back or logged; the
literal-typing checks use the visible User field.
"""

from __future__ import annotations

import time

import pytest
from tests.sap.conftest import _preflight_required, sap_config

from dolphin_desktop import ApplicationError, SapGui

pytestmark = [pytest.mark.external, pytest.mark.timeout(180)]

_USER_IDS = {"RSYST-BNAME", "txtRSYST-BNAME", "ctxtRSYST-BNAME"}
_F_CLIENT = "wnd[0]/usr/txtRSYST-MANDT"
_F_USER = "wnd[0]/usr/txtRSYST-BNAME"
_F_PASSWORD = "wnd[0]/usr/pwdRSYST-BCODE"
_F_LANG = "wnd[0]/usr/txtRSYST-LANGU"
# Twelve characters: the User field is CHAR 12 and SAP upper-cases it.
_LITERAL_PROBE = "{TAB}{ENTER}"


def _skip_or_fail(reason: str) -> None:
    if _preflight_required():
        raise RuntimeError(reason)
    pytest.skip(reason)


@pytest.fixture
def logon_screen(sap_gui):
    """A fresh connection parked on the logon screen; closed again afterwards.

    Opened in addition to whatever the operator already has, never reusing an
    existing window, so a failed test cannot disturb a signed-in session.
    """
    cfg = sap_config()
    if not cfg["connection"]:
        _skip_or_fail("DOLPHIN_SAP_CONNECTION is not set (name of the SAP Logon entry)")
    # Opening a connection raises the SAP GUI "a script is opening a connection"
    # security dialog; the handler dismisses it, but the OpenConnection call can
    # still lose the race once and come back as E_ACCESSDENIED. Retry a few times
    # before giving up so a single popup race is not reported as a hard error.
    session = None
    last_exc: Exception | None = None
    for _ in range(5):
        try:
            session = sap_gui.open_connection(cfg["connection"], timeout=30)
            break
        except Exception as exc:
            last_exc = exc
            time.sleep(1.0)
    else:
        _skip_or_fail(f"could not open the SAP connection (scripting popup race): {last_exc}")
    if session is None:
        _skip_or_fail("scripting session not accessible; enable sapgui/user_scripting")
    try:
        session.wait_until_ready(timeout=30)
        if not session.find_by_id(_F_USER, timeout=5).exists():
            pytest.skip("the new connection did not open on the logon screen (SSO?)")
        yield session
    finally:
        try:
            session.raw.FindById("wnd[0]").Close()
            session.dismiss_all_popups()
        except Exception:
            pass


def _snapshot(session) -> dict[str, str]:
    """Visible logon fields plus the password *length indicator* only."""
    raw = session.raw
    return {
        "client": str(raw.FindById(_F_CLIENT).Text),
        "user": str(raw.FindById(_F_USER).Text),
        "lang": str(raw.FindById(_F_LANG).Text),
        "password_empty": str(raw.FindById(_F_PASSWORD).Text) == "",
        "status": str(raw.FindById("wnd[0]/sbar").Text),
    }


def _window(session):
    from pywinauto import Application

    handle = int(session.raw.FindById("wnd[0]").Handle)
    app = Application(backend="uia")
    app.connect(handle=handle, timeout=15)
    return app.window(handle=handle).wrapper_object()


@pytest.fixture
def uia_inventory(logon_screen):
    """What UIA exposes on the logon screen; the shape keyboard_login() depends on."""
    dlg = _window(logon_screen)
    edits = []
    for field in dlg.descendants(control_type="Edit"):
        info = field.element_info
        try:
            value_pattern = field.iface_value
            writable = not bool(value_pattern.CurrentIsReadOnly)
        except Exception:
            writable = None  # no Value pattern at all
        edits.append(
            {
                "automation_id": str(info.automation_id),
                "is_password": bool(info.element.CurrentIsPassword),
                "writable_value_pattern": writable,
                "has_focus": field.has_keyboard_focus(),
            }
        )
    users = [e for e in edits if e["automation_id"].rsplit("/", 1)[-1] in _USER_IDS]
    passwords = [e for e in edits if e["is_password"]]
    supported = (
        len(users) == 1
        and len(passwords) == 1
        and users[0]["writable_value_pattern"] is True
        and passwords[0]["writable_value_pattern"] is True
    )
    return {
        "title": dlg.window_text(),
        "edits": edits,
        "users": users,
        "passwords": passwords,
        "supported": supported,
    }


def _require_uia_login_fields(inventory) -> None:
    if not inventory["supported"]:
        pytest.skip(
            "this SAP GUI client exposes no User/Password Edit controls to UIA "
            f"(edits seen: {[e['automation_id'] for e in inventory['edits']]}); "
            "keyboard_login() cannot run here and the Scripting login path is required"
        )


# ---------------------------------------------------------------------------
# Step 1 — inventory
# ---------------------------------------------------------------------------


def test_step1_logon_screen_uia_inventory_is_recorded(sap_gui, uia_inventory):
    """Record client version and UIA shape (ids only, no values) for the ticket."""
    com = getattr(sap_gui, "raw", sap_gui)
    version = ".".join(
        str(getattr(com, attr, "?")) for attr in ("MajorVersion", "MinorVersion", "Revision")
    )
    print(f"\nSAP GUI client version: {version}; logon window title: {uia_inventory['title']!r}")
    for edit in uia_inventory["edits"]:
        print(f"  UIA Edit {edit}")
    print(f"  keyboard_login() preconditions met: {uia_inventory['supported']}")

    # Whatever the client exposes, it must be internally consistent: a password
    # control classified as such is never also a User id, and every User
    # candidate is a non-password control.
    for edit in uia_inventory["users"]:
        assert not edit["is_password"]
    assert uia_inventory["title"] == "SAP"


# ---------------------------------------------------------------------------
# Step 5 — safe rejection when UIA does not expose the login fields
# ---------------------------------------------------------------------------


def test_step5_rejects_safely_when_uia_login_fields_are_missing(logon_screen, uia_inventory):
    if uia_inventory["supported"]:
        pytest.skip("UIA exposes the login fields here; see the step 2-4 tests instead")
    before = _snapshot(logon_screen)
    assert before["password_empty"]

    with pytest.raises(ApplicationError):
        SapGui.keyboard_login(uia_inventory["title"], "NOBODY", "not-a-real-secret")

    logon_screen.wait_until_ready(timeout=10)
    after = _snapshot(logon_screen)
    assert after == before, "a rejected login must leave the logon screen untouched"
    assert logon_screen.find_by_id(_F_USER, timeout=2).exists(), "still on the logon screen"


# ---------------------------------------------------------------------------
# Steps 2-4 — only where UIA exposes the fields
# ---------------------------------------------------------------------------


def test_step2_logs_in_with_runtime_credentials(logon_screen, uia_inventory):
    _require_uia_login_fields(uia_inventory)
    cfg = sap_config()
    if not (cfg["user"] and cfg["password"]):
        _skip_or_fail("DOLPHIN_SAP_USER / DOLPHIN_SAP_PASSWORD are not set")
    raw = logon_screen.raw
    if cfg["client"]:
        raw.FindById(_F_CLIENT).Text = cfg["client"]
    raw.FindById(_F_USER).SetFocus()

    SapGui.keyboard_login(uia_inventory["title"], cfg["user"], cfg["password"])

    logon_screen.wait_until_ready(timeout=30)
    logon_screen.dismiss_all_popups()
    info = logon_screen.system_info()
    assert info["user"].upper() == cfg["user"].upper()
    logon_screen.logoff()


def test_step3_sendkeys_metacharacters_are_typed_literally(logon_screen, uia_inventory):
    """The visible User field proves literal typing; the password is never read."""
    _require_uia_login_fields(uia_inventory)
    raw = logon_screen.raw
    raw.FindById(_F_USER).SetFocus()
    before = _snapshot(logon_screen)

    with pytest.raises(ApplicationError):
        # A deliberately wrong password with every SendKeys metacharacter.
        SapGui.keyboard_login(uia_inventory["title"], _LITERAL_PROBE, "%+^{}()~{F1}wrong")

    logon_screen.wait_until_ready(timeout=30)
    after = _snapshot(logon_screen)
    # Enter was pressed once, after both fields were set: SAP answers with a
    # logon error instead of navigating anywhere.
    assert after["user"].strip().upper() == _LITERAL_PROBE.upper()
    assert after["client"] == before["client"]
    assert after["lang"] == before["lang"]
    assert logon_screen.find_by_id(_F_USER, timeout=2).exists(), "still on the logon screen"
    assert after["status"], "SAP reported the rejected logon in the status bar"


@pytest.mark.parametrize("field_id", [_F_CLIENT, _F_LANG])
def test_step4_focus_on_client_or_language_aborts_without_changes(
    logon_screen, uia_inventory, field_id
):
    _require_uia_login_fields(uia_inventory)
    raw = logon_screen.raw
    raw.FindById(field_id).SetFocus()
    before = _snapshot(logon_screen)

    with pytest.raises(ApplicationError, match="focus"):
        SapGui.keyboard_login(uia_inventory["title"], "NOBODY", "not-a-real-secret")

    logon_screen.wait_until_ready(timeout=10)
    assert _snapshot(logon_screen) == before


def test_step4_focus_lost_between_fields_aborts_before_the_password(
    logon_screen, uia_inventory, monkeypatch
):
    """Simulate focus moving past Password: the Tab keyboard_login sends lands on Language."""
    _require_uia_login_fields(uia_inventory)
    import pywinauto.keyboard as keyboard

    real_send_keys = keyboard.send_keys
    monkeypatch.setattr(
        keyboard,
        "send_keys",
        lambda keys, **kw: real_send_keys("{TAB}{TAB}" if keys == "{TAB}" else keys, **kw),
    )
    raw = logon_screen.raw
    raw.FindById(_F_USER).SetFocus()
    before = _snapshot(logon_screen)

    with pytest.raises(ApplicationError, match="focus"):
        SapGui.keyboard_login(uia_inventory["title"], "NOBODY", "not-a-real-secret")

    logon_screen.wait_until_ready(timeout=10)
    after = _snapshot(logon_screen)
    assert after["password_empty"], "no part of the password may reach another control"
    assert after["lang"] == before["lang"]
    assert after["client"] == before["client"]
    assert after["status"] == before["status"], "nothing was submitted"
