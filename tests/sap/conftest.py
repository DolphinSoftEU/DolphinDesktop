"""SAP GUI test fixtures — credentials come from the environment only.

Nothing in this directory contains a user, password or host: the suite
reads them from environment variables and skips cleanly when they are
absent, so the tests are safe to ship and safe to run on a machine that
has no SAP at all.

| Variable                  | Meaning                                    |
| ------------------------- | ------------------------------------------ |
| `DOLPHIN_SAP_USER`        | logon user (required to drive the logon screen) |
| `DOLPHIN_SAP_PASSWORD`    | logon password                             |
| `DOLPHIN_SAP_CLIENT`      | client / mandant, e.g. `001`               |
| `DOLPHIN_SAP_LANG`        | logon language, default `EN`               |
| `DOLPHIN_SAP_CONNECTION`  | SAP Logon entry to open when no session is running |

SAP GUI Scripting must be enabled on both the client (Options → Accessibility
& Scripting → Scripting) and the server (`sapgui/user_scripting = TRUE`).

The fixture attaches to whatever SAP GUI is already running. If that
session sits on the logon screen it signs in; if it is already signed in
it is used as-is, so an operator's open session is never disturbed.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import ApplicationError, SapGui, env_var

pytestmark = pytest.mark.integration

# Logon-screen field ids — identical across releases.
_F_CLIENT = "wnd[0]/usr/txtRSYST-MANDT"
_F_USER = "wnd[0]/usr/txtRSYST-BNAME"
_F_PASSWORD = "wnd[0]/usr/pwdRSYST-BCODE"
_F_LANG = "wnd[0]/usr/txtRSYST-LANGU"


def sap_config() -> dict[str, str]:
    """Environment-provided SAP settings (empty strings when unset)."""
    return {
        "user": env_var("DOLPHIN_SAP_USER") or "",
        "password": env_var("DOLPHIN_SAP_PASSWORD") or "",
        "client": env_var("DOLPHIN_SAP_CLIENT") or "",
        "lang": env_var("DOLPHIN_SAP_LANG") or "EN",
        "connection": env_var("DOLPHIN_SAP_CONNECTION") or "",
    }


def _on_logon_screen(session) -> bool:
    """True when the session shows the SAP logon screen."""
    try:
        return session.find_by_id(_F_USER, timeout=1).exists()
    except Exception:
        return False


def _sign_in(session, cfg: dict[str, str]) -> None:
    if not cfg["user"] or not cfg["password"]:
        pytest.skip(
            "SAP session is on the logon screen but DOLPHIN_SAP_USER / "
            "DOLPHIN_SAP_PASSWORD are not set"
        )
    if cfg["client"]:
        session.find_by_id(_F_CLIENT).set_text(cfg["client"])
    session.find_by_id(_F_USER).set_text(cfg["user"])
    session.find_by_id(_F_PASSWORD).set_text(cfg["password"])
    if cfg["lang"]:
        try:
            session.find_by_id(_F_LANG).set_text(cfg["lang"])
        except Exception:
            pass  # language field is hidden on some logon screens
    session.send_vkey(0)  # Enter
    session.wait_until_ready(timeout=30)
    # A multiple-logon prompt appears when the same user is signed in
    # elsewhere — keeping the existing sessions is the non-destructive
    # choice, so pick "continue with this logon and end the others"
    # only if the popup blocks us; otherwise just dismiss it.
    try:
        session.dismiss_all_popups()
    except Exception:
        pass


@pytest.fixture(scope="session")
def sap_gui():
    """Scripting engine of a running SAP GUI, or skip.

    The security handler starts *before* connecting: with the default SAP
    GUI settings the very first scripting call raises the "a script is
    trying to attach to SAP GUI" notification, and that dialog blocks the
    attach itself — a handler started afterwards would never run.
    """
    stop_security_handler = SapGui.scripting_security_handler()
    try:
        gui = SapGui.connect(timeout=30)
    except ApplicationError as exc:
        stop_security_handler.set()
        pytest.skip(f"SAP GUI Scripting not available: {exc}")
    try:
        yield gui
    finally:
        stop_security_handler.set()


@pytest.fixture(scope="session")
def sap_session(sap_gui):
    """Signed-in :class:`SapSession`, ready for transactions."""
    cfg = sap_config()

    opened_by_us = False
    try:
        session = sap_gui.session(connection=0, session=0)
    except Exception:
        if not cfg["connection"]:
            pytest.skip(
                "no open SAP connection and DOLPHIN_SAP_CONNECTION is not set "
                "(name of the SAP Logon entry to open)"
            )
        session = sap_gui.open_connection(cfg["connection"], timeout=30)
        opened_by_us = True
        if session is None:
            pytest.skip(
                "opened the SAP connection but the scripting session is not "
                "accessible — enable server-side scripting "
                "(sapgui/user_scripting = TRUE)"
            )

    session.wait_until_ready(timeout=30)
    if _on_logon_screen(session):
        _sign_in(session, cfg)

    try:
        yield session
    finally:
        # Close only what this run opened. A session the operator already
        # had on screen is left exactly as found — logging it off would
        # discard their work; the earlier version leaked every connection
        # it opened instead, and they piled up on the desktop.
        if opened_by_us:
            try:
                session.logoff()
            except Exception:
                pass


@pytest.fixture
def sap_home(sap_session):
    """Session parked on the SAP Easy Access screen before and after a test.

    Tests may navigate anywhere; this returns the session to a known
    state so an ordering accident cannot cascade into every later test.
    A modal left over from an earlier test swallows the OK-code field,
    so popups are cleared on the way in as well as on the way out.
    """
    try:
        sap_session.dismiss_all_popups()
    except Exception:
        pass
    yield sap_session
    try:
        sap_session.dismiss_all_popups()
        sap_session.transaction("SESSION_MANAGER")
        sap_session.wait_until_ready(timeout=20)
    except Exception:
        pass
