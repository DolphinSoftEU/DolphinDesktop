"""Steam CEF + CDP fixture.

Two modes:

* **Connect** (default, safe): if a Steam client is already listening on
  the CDP debug port (``8080``), attach to it via
  :meth:`CDPSession.connect`. This is the ONLY safe default — auto-killing
  the user's running Steam session would log them out and lose game state.

* **Launch** (opt-in via ``DOLPHIN_STEAM_ALLOW_LAUNCH=1``): kill any
  running Steam, relaunch with ``-cef-enable-debugging``, wait for the
  port. Requires the user to have accepted the trade-off.

If neither path yields a live session the fixture skips the test with an
explanation of how to enable it.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import CDPSession, Desktop, env_var
from tests.steam._steam_env import (  # type: ignore[import-not-found]
    STEAM,
    STEAM_CDP_ENDPOINT,
    has_playwright,
    steam_cdp_up,
)


def _allow_launch() -> bool:
    return (env_var("DOLPHIN_STEAM_ALLOW_LAUNCH") or "").strip() == "1"


def _preflight_required() -> bool:
    return (env_var("DOLPHIN_STEAM_PREFLIGHT") or "").strip() == "1"


@pytest.fixture(scope="session")
def steam_cdp():
    """Session-scoped ``CDPSession`` attached to Steam's CEF runtime.

    Session scope (not module) because Steam is expensive to launch and
    login state must persist. Every test in the suite shares one session
    and treats it read-only where possible.
    """
    if not has_playwright():
        if _preflight_required():
            raise RuntimeError("dolphin_desktop[cdp] extra not installed")
        pytest.skip("dolphin_desktop[cdp] extra not installed")
    if STEAM is None:
        if _preflight_required():
            raise RuntimeError("Steam not installed")
        pytest.skip("Steam not installed")

    if steam_cdp_up():
        try:
            cdp = CDPSession.connect(STEAM_CDP_ENDPOINT, timeout=10)
        except Exception as exc:
            if _preflight_required():
                raise
            pytest.skip(f"Steam CDP port is open but connect failed: {exc}")
        try:
            yield cdp
        finally:
            try:
                cdp.close()
            except Exception:
                pass
        return

    if not _allow_launch():
        if _preflight_required():
            raise RuntimeError("Steam CDP port 8080 is not open")
        pytest.skip(
            "Steam CDP port 8080 is not open. Either:\n"
            "  1) start Steam manually with `-cef-enable-debugging`, OR\n"
            "  2) set DOLPHIN_STEAM_ALLOW_LAUNCH=1 to let the fixture "
            "kill and relaunch Steam (will end your current Steam session)."
        )

    # Opt-in launch path — assumes user accepted killing existing Steam.
    from dolphin_desktop import find_pid_by_image_name

    existing_pid = find_pid_by_image_name("steam.exe")
    if existing_pid is not None:
        try:
            desktop = Desktop()
            existing = desktop.connect(process=existing_pid, timeout=5)
            existing.kill()
        except Exception:
            pass

    desktop = Desktop()
    try:
        app, cdp = desktop.launch_cef_cdp(f'"{STEAM}"', timeout=45, startup_delay=3.0)
    except Exception as exc:
        if _preflight_required():
            raise
        pytest.skip(f"Steam launch with CDP failed: {exc}")

    app.detach()
    try:
        yield cdp
    finally:
        try:
            cdp.close()
        except Exception:
            pass
        try:
            app.kill()
        except Exception:
            pass
