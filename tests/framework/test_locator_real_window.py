"""Locator resolution against a real window — no mocks anywhere in the chain.

Every other locator test in this directory substitutes a ``MagicMock`` for
pywinauto and therefore asserts "we passed these criteria", never "these
criteria found the element". This module runs the whole chain for real:
criteria → ``child_window`` → ``find_elements`` → UIA → a live HWND, against
the plain user32 window built by :mod:`tests.framework._real_window`.

Requires a desktop session, so the module is marked ``integration``. Every
resolution is a cross-process UIA round trip costing a few seconds, which is
why the module raises the repository's 30 s per-test timeout.
"""

from __future__ import annotations

import pytest

from dolphin_desktop import (
    AmbiguousMatchError,
    Desktop,
    ElementNotFoundError,
    WaitTimeoutError,
    dirname,
    path_join,
    python_executable,
    sleep,
)
from tests.framework._real_window import (  # type: ignore[import-not-found]
    EDIT_INITIAL_TEXT,
    ID_CANCEL,
    ID_CHECKBOX,
    ID_EDIT,
    ID_OK,
    ID_OK_DUPLICATE,
    ID_STATIC,
    STATUS_CLICKED,
    STATUS_IDLE,
    WINDOW_TITLE,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(120)]

_HELPER = path_join(dirname(__file__), "_real_window.py")


@pytest.fixture(scope="module")
def window():
    """The real helper window, launched once and killed after the module."""
    desktop = Desktop(backend="uia")
    app = desktop.launch(f'"{python_executable()}" "{_HELPER}"')
    try:
        win = app.window(title=WINDOW_TITLE, timeout=20)
        # Module scope outlives dolphin's per-test PID reaping.
        app.detach()
        yield win
    finally:
        app.kill()


@pytest.fixture
def idle_status(window):
    """Drive the STATIC back to its idle caption before the test body runs."""
    window.locator(auto_id=str(ID_CANCEL)).invoke()
    window.locator(auto_id=str(ID_STATIC)).wait_for_text(STATUS_IDLE, timeout=10)


@pytest.fixture
def prefilled_edit(window):
    """Restore the EDIT to the text the helper starts it with."""
    window.locator(auto_id=str(ID_EDIT)).set_text(EDIT_INITIAL_TEXT)


# Resolution by each criteria family


class TestResolutionByCriteria:
    def test_title_resolves_the_named_button(self, window):
        """A unique caption resolves to the control carrying it."""
        assert window.locator(title="Cancel").exists() is True

    def test_class_name_resolves_the_edit(self, window):
        """class_name="Edit" reaches the EDIT control, not a Button."""
        loc = window.locator(class_name="Edit")
        assert loc.get_attribute("automation_id") == str(ID_EDIT)

    def test_control_type_resolves_the_static_as_text(self, window, idle_status):
        """A user32 STATIC is published by UIA with control_type "Text"."""
        loc = window.locator(control_type="Text", title=STATUS_IDLE)
        assert loc.get_attribute("automation_id") == str(ID_STATIC)

    def test_auto_id_is_the_win32_control_id(self, window):
        """The hMenu control id reaches UIA as the element's AutomationId."""
        assert window.locator(auto_id=str(ID_OK)).text() == "OK"

    def test_get_by_class_resolves_the_static(self, window, idle_status):
        """get_by_class() reaches the STATIC through its user32 window class."""
        assert window.get_by_class("Static").text() == STATUS_IDLE


# Text reading and writing on the real EDIT


@pytest.mark.usefixtures("prefilled_edit")
class TestEditText:
    def test_text_reads_the_edit_content(self, window):
        """text() returns the EDIT's live content, read through ValuePattern."""
        assert window.locator(auto_id=str(ID_EDIT)).text() == EDIT_INITIAL_TEXT

    def test_set_text_replaces_and_reads_back(self, window):
        """set_text() writes through ValuePattern and text() sees the new value."""
        edit = window.locator(auto_id=str(ID_EDIT))
        edit.set_text("replaced by set_text")
        assert edit.text() == "replaced by set_text"

    def test_type_text_appends_and_reads_back(self, window):
        """type_text() delivers real keystrokes into the emptied EDIT."""
        edit = window.locator(auto_id=str(ID_EDIT))
        edit.set_text("")
        edit.type_text("typed")
        assert edit.text() == "typed"

    def test_clear_empties_the_control(self, window):
        """clear() leaves the EDIT with an empty value."""
        edit = window.locator(auto_id=str(ID_EDIT))
        edit.set_text("something")
        edit.clear()
        assert edit.text() == ""


# Programmatic actions


@pytest.mark.usefixtures("idle_status")
class TestInvokeAndToggle:
    def test_invoke_fires_the_ok_button(self, window):
        """invoke() reaches the window procedure — the STATIC caption changes."""
        window.locator(auto_id=str(ID_OK)).invoke()
        sleep(0.3)
        assert window.locator(auto_id=str(ID_STATIC)).text() == STATUS_CLICKED

    def test_invoke_on_the_duplicate_button_fires_it_too(self, window):
        """The second "OK" is a distinct control with its own id and effect."""
        window.locator(auto_id=str(ID_OK_DUPLICATE)).invoke()
        sleep(0.3)
        assert window.locator(auto_id=str(ID_STATIC)).text() == STATUS_CLICKED

    def test_toggle_round_trips_the_checkbox(self, window):
        """toggle() flips the BS_AUTOCHECKBOX and is_checked() reads it back."""
        cb = window.locator(auto_id=str(ID_CHECKBOX))
        start = cb.is_checked()
        cb.toggle()
        assert cb.is_checked() is not start
        cb.toggle()
        assert cb.is_checked() is start

    def test_check_and_uncheck_reach_a_known_state(self, window):
        """check()/uncheck() drive the box to an absolute state, not a relative one."""
        cb = window.locator(auto_id=str(ID_CHECKBOX))
        cb.check()
        assert cb.is_checked() is True
        cb.uncheck()
        assert cb.is_checked() is False


# Ambiguity — two real controls share the caption "OK"


class TestAmbiguity:
    def test_duplicate_caption_raises_ambiguous_match_error(self, window):
        """Criteria matching both OK buttons raise instead of picking one."""
        with pytest.raises(AmbiguousMatchError, match="found_index"):
            window.locator(title="OK", control_type="Button")._resolve()

    def test_found_index_disambiguates(self, window):
        """found_index= selects one of the duplicates by position."""
        first = window.locator(title="OK", control_type="Button", found_index=0)
        second = window.locator(title="OK", control_type="Button", found_index=1)
        ids = {first.get_attribute("automation_id"), second.get_attribute("automation_id")}
        assert ids == {str(ID_OK), str(ID_OK_DUPLICATE)}

    def test_nth_disambiguates(self, window):
        """nth() is the fluent spelling of found_index and resolves the same way."""
        loc = window.locator(title="OK", control_type="Button").nth(1)
        assert loc.get_attribute("automation_id") in (str(ID_OK), str(ID_OK_DUPLICATE))

    def test_exists_is_true_for_the_ambiguous_criteria(self, window):
        """Two matches still means the element is present."""
        assert window.locator(title="OK", control_type="Button").exists() is True


# Presence


class TestExists:
    def test_exists_true_for_a_present_control(self, window):
        """A control that is on screen reports True."""
        assert window.locator(auto_id=str(ID_CHECKBOX)).exists() is True

    def test_exists_false_for_an_absent_control(self, window):
        """Criteria that match nothing report False instead of raising."""
        assert window.locator(auto_id="no-such-control").exists(timeout=0.5) is False

    def test_resolve_raises_element_not_found_for_an_absent_control(self, window):
        """The same criteria raise when an action needs the element."""
        with pytest.raises(ElementNotFoundError):
            window.locator(auto_id="no-such-control").timeout(0.5)._resolve()


# Attribute reads against a real element_info


class TestGetAttribute:
    def test_returns_the_published_value(self, window):
        """A name the element publishes comes back as its value."""
        assert window.locator(auto_id=str(ID_OK)).get_attribute("name") == "OK"

    def test_raises_for_a_name_the_element_does_not_publish(self, window):
        """An unpublished name raises instead of reading back as None.

        The PascalCase UIA spelling is the realistic typo: pywinauto
        publishes ``automation_id``, so ``AutomationId`` matches nothing.
        """
        with pytest.raises(AttributeError, match="AutomationId"):
            window.locator(auto_id=str(ID_OK)).get_attribute("AutomationId")

    def test_error_lists_the_names_that_are_published(self, window):
        """The message names a real attribute so the typo is fixable."""
        with pytest.raises(AttributeError, match="automation_id"):
            window.locator(auto_id=str(ID_OK)).get_attribute("AutomationId")

    def test_default_suppresses_the_raise(self, window):
        """An explicit default opts back into the non-raising lookup."""
        loc = window.locator(auto_id=str(ID_OK))
        assert loc.get_attribute("AutomationId", None) is None
        assert loc.get_attribute("no_such_field", "fallback") == "fallback"

    def test_default_does_not_shadow_a_published_value(self, window):
        """Passing a default still returns the real value when there is one."""
        assert window.locator(auto_id=str(ID_OK)).get_attribute("name", "x") == "OK"


# Fallback selectors


class TestFallback:
    def test_fallback_resolves_when_the_primary_criteria_cannot_match(self, window):
        """A stale primary selector is rescued by the fallback entry."""
        loc = window.locator(
            auto_id="renamed-in-a-refactor",
            fallback=[{"auto_id": str(ID_CANCEL)}],
        ).timeout(0.5)
        assert loc.text() == "Cancel"

    def test_fallback_is_skipped_when_the_primary_matches(self, window):
        """The fallback must not shadow a primary selector that still works."""
        loc = window.locator(
            auto_id=str(ID_CHECKBOX),
            fallback=[{"auto_id": str(ID_CANCEL)}],
        )
        assert loc.text() == "Remember me"

    def test_all_fallbacks_failing_raises_element_not_found(self, window):
        """A fallback chain that matches nothing still ends in a not-found error."""
        loc = window.locator(
            auto_id="renamed-in-a-refactor",
            fallback=[{"auto_id": "also-gone"}],
        ).timeout(0.5)
        with pytest.raises(ElementNotFoundError):
            loc._resolve()


# Auto-waiting on the text a real action produced


@pytest.mark.usefixtures("idle_status")
class TestWaitForText:
    def test_wait_for_text_returns_after_the_invoke_lands(self, window):
        """The STATIC reaches "Status: clicked" without a fixed sleep."""
        status = window.locator(auto_id=str(ID_STATIC))
        window.locator(auto_id=str(ID_OK)).invoke()
        status.wait_for_text(STATUS_CLICKED, timeout=5)
        assert status.text() == STATUS_CLICKED

    def test_wait_for_text_times_out_when_the_text_never_changes(self, window):
        """No action fires, so the caption stays idle and the wait raises."""
        status = window.locator(auto_id=str(ID_STATIC))
        with pytest.raises(WaitTimeoutError, match=STATUS_IDLE):
            status.wait_for_text(STATUS_CLICKED, timeout=1)
