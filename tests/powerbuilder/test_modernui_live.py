"""Live tests against Appeon's ModernUI demo (PowerClient deployment).

Opt-in by presence: the suite attaches to a running ``modernui.exe`` and
skips when none is found. Start the app and open the **Address** window
(Logistics ribbon tab) with its **Browse** tab visible before running.

These tests drive a real classic-PB surface end-to-end using only the
public dolphin API, exercising the full fallback cascade the library
documents for PowerBuilder (`docs/guides/powerbuilder.md`):

* window chrome + standard controls (Button/Edit) via UIA,
* the opaque DataWindow (class ``pbdw``) via OCR (`Screen.find_text` /
  `Screen.text`) and coordinate input (`Mouse`, `Keyboard`).

They are **physical-input** tests: they move the real mouse and type on
the real keyboard, so run them on an unattended desktop.

Data safety: only the Browse tab's *filters* are touched (State/Province
combo + "Filter by City" box). Nothing is saved; every test restores the
filter state it changed.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import (
    Desktop,
    Mouse,
    Screen,
    find_pid_by_image_name,
    sleep,
)

pytestmark = [pytest.mark.system, pytest.mark.timeout(120)]

_PID = find_pid_by_image_name("modernui.exe")

skip_no_modernui = pytest.mark.skipif(
    _PID is None,
    reason="ModernUI demo not running — start it and open Address > Browse",
)

# Combo arrow glyph sits this far right of the OCR centre of the
# "State/Province" label on the Browse filter row (measured at 100% DPI).
_ARROW_DX = 164
# The dropped list opens directly under the filter row.
_LIST_STRIP = (0, 10, 420, 340)  # dx_left, dy_top, dx_right, dy_bottom


@pytest.fixture(scope="module")
def modernui():
    """Attached app + main window + Browse-pane region, focused once."""
    app = Desktop().connect(process=_PID, timeout=10)
    win = app.window(title_re=".*ModernUI App.*", timeout=10)
    win.focus()
    sleep(0.4)
    pane = win.locator(class_name="FNUDO3", found_index=0)
    bb = pane.bounding_box()
    region = (bb["left"], bb["top"], bb["right"], bb["bottom"])
    if Screen.find_text("State/Province", region=region) is None:
        pytest.skip("Address window with the Browse tab is not visible")
    return app, win, region


def _state_label(region):
    pt = Screen.find_text("State/Province", region=region)
    assert pt is not None, "State/Province filter label not found via OCR"
    return pt


def _select_state(region, state: str) -> None:
    """Open the State/Province combo and pick *state* from the list."""
    lbl = _state_label(region)
    Mouse.click(lbl[0] + _ARROW_DX, lbl[1])
    sleep(0.9)
    strip = (
        lbl[0] + _LIST_STRIP[0],
        lbl[1] + _LIST_STRIP[1],
        lbl[0] + _LIST_STRIP[2],
        lbl[1] + _LIST_STRIP[3],
    )
    pt = Screen.find_text(state, region=strip)
    assert pt is not None, (
        f"{state!r} not found in the dropped list — either the list did not "
        f"open or OCR failed even after upscale retries"
    )
    Mouse.click(*pt)
    sleep(1.2)


def _filter_row_text(region) -> str:
    lbl = _state_label(region)
    return Screen.text(region=(lbl[0] - 80, lbl[1] - 15, lbl[0] + 320, lbl[1] + 15))


# ---------------------------------------------------------------------------
# UIA layer — chrome and standard controls of a classic-PB window
# ---------------------------------------------------------------------------


@skip_no_modernui
def test_chrome_and_buttons_resolve_via_uia(modernui):
    _app, win, _region = modernui
    assert win.is_visible()
    assert "ModernUI App" in win.title()
    for name in ("Save", "Delete", "Add"):
        assert win.button(name=name).exists(), f"{name} button not exposed via UIA"


@skip_no_modernui
def test_city_filter_edit_roundtrip_via_uia(modernui):
    """The 'Filter by City' box is a real Edit — type, read back, clear."""
    _app, win, region = modernui
    edit = win.locator(control_type="Edit")
    edit.type_text("Edmonton")
    edit.wait_for_text("Edmonton", timeout=5)
    sleep(1.0)  # let the grid re-filter
    try:
        grid_text = Screen.text(region=region)
        assert "Edmonton" in grid_text, "filtered grid does not show Edmonton rows"
        assert "Calgary" not in grid_text, "Calgary rows survived the Edmonton filter"
    finally:
        edit.set_value("")
        sleep(0.8)


# ---------------------------------------------------------------------------
# OCR layer — the opaque DataWindow filter combo
# ---------------------------------------------------------------------------


@skip_no_modernui
def test_state_filter_dropdown_alaska_empties_grid(modernui):
    """Pick Alaska from the dropdown: combo updates, grid loses its rows."""
    _app, _win, region = modernui
    try:
        _select_state(region, "Alaska")
        assert "Alaska" in _filter_row_text(region)
        assert "Calgary" not in Screen.text(region=region), (
            "grid still shows Alberta rows after filtering to Alaska"
        )
    finally:
        _select_state(region, "Alberta")


@skip_no_modernui
def test_state_filter_restored_to_alberta_repopulates_grid(modernui):
    """Back on Alberta the filter row reads Alberta and Calgary is visible."""
    _app, _win, region = modernui
    assert "Alberta" in _filter_row_text(region)
    grid_text = Screen.text(region=region)
    assert "Calgary" in grid_text, "Alberta filter shows no Calgary rows"
