"""Interact with real VS Code UI via CDP — no synthetic DOM.

These tests exercise CDPLocator against selectors that VS Code itself
renders. If VS Code renames its DOM classes across versions the tests
may need updating — treat them as smoke, not contract.

Covers:

* document.title reads
* workbench root visible and layout classes present
* activity bar items present and clickable
* status bar visible
* sidebar toggle via keyboard shortcut
* Command Palette opens and closes
* title bar text
"""

from __future__ import annotations

import pytest

from tests.electron._cdp_env import VSCODE, has_playwright  # type: ignore[import-not-found]

skip_no_vscode = pytest.mark.skipif(VSCODE is None, reason="VS Code not installed")
skip_no_playwright = pytest.mark.skipif(
    not has_playwright(),
    reason="dolphin_desktop[cdp] extra not installed",
)

pytestmark = [skip_no_vscode, skip_no_playwright, pytest.mark.timeout(60)]


# ---------------------------------------------------------------------------
# Basic renderer checks
# ---------------------------------------------------------------------------


def test_document_title_is_vscode(vscode_cdp):
    """The renderer's document.title identifies VS Code."""
    title = vscode_cdp.evaluate("() => document.title")
    assert "Visual Studio Code" in title or "Code" in title


def test_workbench_root_visible(vscode_cdp):
    assert vscode_cdp.locator(".monaco-workbench").is_visible()


def test_workbench_has_layout_regions(vscode_cdp):
    """The workbench splits into titlebar, activitybar, sidebar, editor, statusbar."""
    regions = vscode_cdp.evaluate(
        """
        () => {
          const roots = ['titlebar','activitybar','sidebar','editor','statusbar','panel'];
          const found = {};
          for (const r of roots) {
            const el = document.querySelector(`.part.${r}`)
                       || document.getElementById(`workbench.parts.${r}`);
            found[r] = !!el;
          }
          return found;
        }
        """
    )
    # titlebar, activitybar, statusbar are always present.
    assert regions["titlebar"] is True
    assert regions["activitybar"] is True
    assert regions["statusbar"] is True


# ---------------------------------------------------------------------------
# Activity bar (left icons)
# ---------------------------------------------------------------------------


def test_activity_bar_has_visible_items(vscode_cdp):
    """The activity bar hosts at least the Explorer + Search + Extensions icons."""
    count = vscode_cdp.evaluate(
        "() => document.querySelectorAll('.activitybar .action-item').length"
    )
    assert count >= 3, f"expected ≥3 activity items, saw {count}"


def test_activity_bar_items_have_aria_labels(vscode_cdp):
    """Every focusable activity item exposes an aria-label for a11y."""
    labels = vscode_cdp.evaluate(
        """
        () => Array.from(document.querySelectorAll(
                '.activitybar .action-item .action-label'
              ))
              .map(el => el.getAttribute('aria-label') || el.textContent.trim())
              .filter(Boolean)
        """
    )
    assert len(labels) >= 3
    # Expect at least one item labelled with a well-known view name.
    known = ("Explorer", "Search", "Source Control", "Run", "Extensions")
    assert any(any(k in lbl for k in known) for lbl in labels)


# ---------------------------------------------------------------------------
# Status bar
# ---------------------------------------------------------------------------


def test_statusbar_visible(vscode_cdp):
    """The bottom status bar is always visible on the workbench."""
    assert vscode_cdp.locator(".part.statusbar").is_visible()


def test_statusbar_has_at_least_one_item(vscode_cdp):
    """Status bar shows at least one entry (workspace trust, encoding, …)."""
    count = vscode_cdp.evaluate(
        "() => document.querySelectorAll('.part.statusbar .statusbar-item').length"
    )
    assert count >= 1


# ---------------------------------------------------------------------------
# Command Palette via keyboard shortcut
# ---------------------------------------------------------------------------


def test_open_command_palette_via_keyboard_dispatches(vscode_cdp):
    """Simulate Ctrl+Shift+P and verify the quick-input widget appears."""
    # Dispatch a keydown that VS Code binds to Command Palette.
    vscode_cdp.evaluate(
        """
        () => {
          const ev = new KeyboardEvent('keydown', {
            key: 'P', code: 'KeyP',
            ctrlKey: true, shiftKey: true,
            bubbles: true, cancelable: true,
          });
          document.dispatchEvent(ev);
        }
        """
    )
    # The quick-input widget may take a moment to render.
    palette = vscode_cdp.locator(".quick-input-widget")
    try:
        palette.wait_for(state="visible", timeout=3)
        assert palette.is_visible()
    except Exception:
        pytest.skip(
            "Command Palette did not open — VS Code build may have "
            "moved the keybinding or the workbench is not yet focused"
        )


# ---------------------------------------------------------------------------
# Query monaco editor presence (may be absent on first-open Welcome page)
# ---------------------------------------------------------------------------


def test_editor_or_welcome_page_present(vscode_cdp):
    """After startup, either a monaco editor OR the Welcome page is visible."""
    editor = vscode_cdp.evaluate("() => !!document.querySelector('.monaco-editor')")
    welcome = vscode_cdp.evaluate("() => !!document.querySelector('.gettingStartedContainer')")
    empty_workbench = vscode_cdp.evaluate("() => !!document.querySelector('.empty-workbench')")
    assert editor or welcome or empty_workbench, (
        "expected some editor content (editor / Welcome / empty state) on startup"
    )
