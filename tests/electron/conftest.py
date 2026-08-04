"""Shared VS Code + CDP fixture for the Electron CDP integration tests.

All test files in this directory share:

* :func:`vscode_cdp` — module-scoped ``CDPSession`` connected to a fresh
  VS Code launched via :meth:`Desktop.launch_electron_cdp`.
* :data:`VSCODE`, :func:`has_playwright` (from :mod:`tests.electron._cdp_env`)
  — reused skip predicates.

Module scope keeps VS Code alive across every test in a file (one launch
per file) while isolating each module — one file's DOM edits do not leak
into the next. Each fixture picks a unique CDP port so parallel runs do
not collide.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import Desktop, counter, tempdir
from tests.electron._cdp_env import VSCODE, has_playwright  # type: ignore[import-not-found]

_port_counter = counter(9230)


def _next_port() -> int:
    return next(_port_counter)


@pytest.fixture(scope="module")
def vscode_cdp():
    """Fresh VS Code + CDP session, torn down at the end of the module."""
    if VSCODE is None:
        pytest.skip("VS Code not installed")
    if not has_playwright():
        pytest.skip("dolphin_desktop[cdp] extra not installed")

    port = _next_port()
    with tempdir(prefix="dolphin_vscode_cdp_") as tmpdir:
        cmd = (
            f'"{VSCODE}" '
            f'--user-data-dir="{tmpdir}" '
            f'--extensions-dir="{tmpdir}\\ext" '
            "--no-sandbox "
            "--disable-workspace-trust "
            "--disable-gpu"
        )
        desktop = Desktop()
        app, cdp = desktop.launch_electron_cdp(cmd, debug_port=port, timeout=30)
        # Opt out of dolphin's per-test PID reaping — module scope requires it.
        app.detach()
        try:
            cdp.locator(".monaco-workbench").wait_for(state="visible", timeout=30)
        except Exception:
            pass
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


@pytest.fixture(autouse=True)
def _cleanup_synthetic_dom(vscode_cdp):
    """Wipe test-injected DOM before each test to prevent z-index collisions.

    Tests inject synthetic elements via ``vscode_cdp.evaluate("...")``.
    Because the CDP session is module-scoped, later tests inherit the DOM
    of earlier ones — same coordinates end up stacked, and Playwright's
    click-actionability check refuses to click a covered element.

    Synthetic elements are appended to ``<body>``, so we rip out every
    direct child of ``<body>`` except the top-level VS Code renderer roots
    (``.monaco-workbench``, ``.monaco-shell``, ``#workbench.parts``...).
    """
    # Best-effort: if the session is dead we just yield and let the test
    # fail with its own error.
    try:
        vscode_cdp.evaluate(
            """
            () => {
              // Remove every direct child of <body> that is NOT one of the
              // VS Code renderer roots. VS Code renders through a single
              // top-level container ('.monaco-workbench'); everything else
              // is fair game.
              const doomed = [];
              for (const el of Array.from(document.body.children)) {
                if (el.classList && (
                      el.classList.contains('monaco-workbench') ||
                      el.classList.contains('monaco-shell') ||
                      el.classList.contains('monaco-grid-view'))) {
                  continue;
                }
                if (el.id && el.id.startsWith('workbench.')) continue;
                if (el.tagName === 'SCRIPT' || el.tagName === 'STYLE'
                    || el.tagName === 'LINK') continue;
                doomed.push(el);
              }
              doomed.forEach(el => el.remove());
            }
            """
        )
    except Exception:
        pass
    yield
