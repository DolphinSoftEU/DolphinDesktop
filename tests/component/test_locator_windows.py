"""Minimal real-window contracts for the public Windows locator API."""

from __future__ import annotations

import pytest
from tests.component._real_window import (
    ID_CANCEL,
    ID_OK,
    ID_STATUS,
    STATUS_CLICKED,
    WINDOW_TITLE,
)

from dolphin_desktop import (
    Desktop,
    dirname,
    path_join,
    python_executable,
)

pytestmark = [pytest.mark.windows_component, pytest.mark.timeout(120)]

_HELPER = path_join(dirname(__file__), "_real_window.py")


@pytest.fixture(scope="module")
def real_window():
    desktop = Desktop(backend="uia")
    app = desktop.launch(f'"{python_executable()}" "{_HELPER}"', startup_delay=0.5)
    try:
        window = app.window(title=WINDOW_TITLE, timeout=20)
        app.detach()
        yield window
    finally:
        app.kill()


def test_real_locator_uses_a_fallback_selector(real_window) -> None:
    locator = real_window.locator(
        auto_id="stale-after-refactor",
        fallback=[{"auto_id": str(ID_CANCEL)}],
    )

    assert locator.text() == "Cancel"


def test_real_locator_collection_filters_uia_auto_id_after_backend_fallback(real_window) -> None:
    matches = real_window.locator(auto_id=str(ID_CANCEL)).all(depth=5)

    assert [match.text() for match in matches] == ["Cancel"]


def test_real_locator_get_attribute_default_is_used_for_missing_attribute(real_window) -> None:
    locator = real_window.locator(auto_id=str(ID_OK))

    assert locator.get_attribute("not_published", "fallback") == "fallback"


def test_real_locator_attribute_error_lists_available_names(real_window) -> None:
    locator = real_window.locator(auto_id=str(ID_OK))

    with pytest.raises(AttributeError, match="automation_id"):
        locator.get_attribute("AutomationId")


def test_real_locator_wait_for_text_succeeds_after_a_state_change(real_window) -> None:
    real_window.locator(auto_id=str(ID_CANCEL)).invoke()
    real_window.locator(auto_id=str(ID_OK)).invoke()

    status = real_window.locator(auto_id=str(ID_STATUS))
    assert status.wait_for_text(STATUS_CLICKED, timeout=5) is status
