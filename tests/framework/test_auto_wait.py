"""Tests for auto-waiting and timeout configuration."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

import dolphin_desktop
import dolphin_desktop._config as _cfg
import dolphin_desktop._locator as locator_module
from dolphin_desktop import ElementNotFoundError
from dolphin_desktop._locator import Locator, _ResolvedLocator
from dolphin_desktop._window import Window


def _window(spec: MagicMock | None = None) -> Window:
    """Return a Window backed by *spec* (or a fresh MagicMock if omitted)."""
    return Window(spec if spec is not None else MagicMock())


def _failing_spec() -> MagicMock:
    """Return a mock spec whose element never resolves.

    ``wrapper_object`` is what raises, which is how pywinauto reports an
    absent element — the resolve path probes visibility through it.
    """
    spec = MagicMock()
    child = MagicMock()
    child.wrapper_object.side_effect = Exception("element not visible")
    spec.child_window.return_value = child
    spec.children.return_value = []
    return spec


def _succeeding_spec() -> MagicMock:
    """Return a mock spec whose element resolves and reports itself visible."""
    spec = MagicMock()
    child = MagicMock()
    child.wrapper_object.return_value.is_visible.return_value = True
    spec.child_window.return_value = child
    spec.children.return_value = []
    return spec


# dolphin_desktop.config() code configuration


class TestConfigCode:
    def test_config_sets_timeout(self):
        original = _cfg.get_timeout()
        try:
            dolphin_desktop.config(timeout=3.0)
            assert _cfg.get_timeout() == pytest.approx(3.0)
        finally:
            _cfg._defaults["timeout"] = original

    def test_config_sets_poll_interval(self):
        original = _cfg.get_poll_interval()
        try:
            dolphin_desktop.config(poll_interval=0.25)
            assert _cfg.get_poll_interval() == pytest.approx(0.25)
        finally:
            _cfg._defaults["poll_interval"] = original

    def test_config_noop_when_no_args(self):
        before_timeout = _cfg.get_timeout()
        before_poll = _cfg.get_poll_interval()
        dolphin_desktop.config()
        assert _cfg.get_timeout() == pytest.approx(before_timeout)
        assert _cfg.get_poll_interval() == pytest.approx(before_poll)

    def test_config_accepts_int(self):
        original = _cfg.get_timeout()
        try:
            dolphin_desktop.config(timeout=5)
            assert _cfg.get_timeout() == 5.0
            assert isinstance(_cfg.get_timeout(), float)
        finally:
            _cfg._defaults["timeout"] = original

    def test_get_timeout_returns_float(self):
        assert isinstance(_cfg.get_timeout(), float)

    def test_get_poll_interval_returns_float(self):
        assert isinstance(_cfg.get_poll_interval(), float)

    def test_poll_interval_default_is_100ms(self):
        assert _cfg.get_poll_interval() == pytest.approx(0.1)


# DOLPHIN_TIMEOUT environment variable configuration


class TestConfigEnvVar:
    def test_env_var_sets_timeout_on_module_load(self, monkeypatch):
        """DOLPHIN_TIMEOUT is read at _config import time; reload confirms the mapping."""
        from importlib import reload

        original = dict(_cfg._defaults)
        monkeypatch.setenv("DOLPHIN_TIMEOUT", "7.5")
        try:
            reload(_cfg)
            assert _cfg._defaults["timeout"] == pytest.approx(7.5)
        finally:
            _cfg._defaults.update(original)

    def test_env_var_absent_gives_default(self, monkeypatch):
        from importlib import reload

        original = dict(_cfg._defaults)
        monkeypatch.delenv("DOLPHIN_TIMEOUT", raising=False)
        try:
            reload(_cfg)
            assert _cfg._defaults["timeout"] == pytest.approx(10.0)
        finally:
            _cfg._defaults.update(original)

    def test_env_var_float_string(self, monkeypatch):
        from importlib import reload

        original = dict(_cfg._defaults)
        monkeypatch.setenv("DOLPHIN_TIMEOUT", "2.5")
        try:
            reload(_cfg)
            assert _cfg._defaults["timeout"] == pytest.approx(2.5)
        finally:
            _cfg._defaults.update(original)


# @pytest.mark.dolphin(timeout=X) marker configuration


@pytest.mark.dolphin(timeout=1.5)
def test_marker_overrides_timeout_during_test():
    assert _cfg.get_timeout() == pytest.approx(1.5)


@pytest.mark.dolphin(timeout=0.5)
def test_marker_uses_fractional_timeout():
    assert _cfg.get_timeout() == pytest.approx(0.5)


def test_marker_timeout_is_restored_after_marked_test():
    # If the fixture rolls back correctly, the current timeout must differ from
    # every marker value used above (1.5, 0.5).
    current = _cfg.get_timeout()
    assert current != pytest.approx(1.5)
    assert current != pytest.approx(0.5)


# Locator timeout propagation


class TestLocatorTimeout:
    def test_new_locator_uses_global_timeout(self):
        original = _cfg.get_timeout()
        try:
            _cfg._defaults["timeout"] = 8.0
            loc = Locator(_window())
            assert loc._timeout == pytest.approx(8.0)
        finally:
            _cfg._defaults["timeout"] = original

    def test_timeout_clone_has_new_value(self):
        loc = Locator(_window())
        clone = loc.timeout(2.0)
        assert clone._timeout == pytest.approx(2.0)

    def test_timeout_clone_does_not_mutate_original(self):
        loc = Locator(_window())
        original_t = loc._timeout
        loc.timeout(99.0)
        assert loc._timeout == pytest.approx(original_t)

    def test_resolved_locator_timeout_clone_is_resolved_locator(self):
        rl = _ResolvedLocator(MagicMock())
        clone = rl.timeout(3.0)
        assert isinstance(clone, _ResolvedLocator)
        assert clone._timeout == pytest.approx(3.0)

    def test_resolved_locator_timeout_does_not_mutate_original(self):
        rl = _ResolvedLocator(MagicMock())
        t0 = rl._timeout
        rl.timeout(77.0)
        assert rl._timeout == pytest.approx(t0)


class TestLocatorCloneKeepsType:
    """timeout()/nth() must not downgrade a subclass to a plain Locator.

    A MenuItem that loses its class also loses ``_resolve`` (which opens the
    parent menu), so ``menu(...).item(...).click(timeout_ms=...)`` would fail
    while the same call without a timeout works.
    """

    def test_timeout_keeps_menu_item_type(self):
        from dolphin_desktop._element import MenuItem

        item = _window().menu("File").item("Save")
        assert isinstance(item.timeout(5.0), MenuItem)

    def test_timeout_keeps_element_subclass_type(self):
        from dolphin_desktop._element import ComboBox, RadioButton, Tree

        win = _window()
        assert isinstance(win.combo_box(name="Language").timeout(1.0), ComboBox)
        assert isinstance(win.radio_button(name="A").timeout(1.0), RadioButton)
        assert isinstance(win.tree().timeout(1.0), Tree)

    def test_nth_keeps_subclass_type(self):
        from dolphin_desktop._element import Button

        clone = _window().button(name="OK").nth(2)
        assert isinstance(clone, Button)
        assert clone._criteria["found_index"] == 2

    def test_timeout_keeps_criteria_and_fallbacks(self):
        sentinel = object()
        loc = Locator(
            _window(),
            title="OK",
            fallback=[{"auto_id": "btnOk"}],
            image_fallback=sentinel,
        )
        clone = loc.timeout(3.0)
        assert clone._criteria == {"title": "OK"}
        assert clone._fallback == [{"auto_id": "btnOk"}]
        assert clone._image_fallback is sentinel

    def test_nth_keeps_fallbacks_and_timeout(self):
        sentinel = object()
        loc = Locator(
            _window(),
            title="OK",
            fallback=[{"auto_id": "btnOk"}],
            image_fallback=sentinel,
        ).timeout(4.5)
        clone = loc.nth(1)
        assert clone._fallback == [{"auto_id": "btnOk"}]
        assert clone._image_fallback is sentinel
        assert clone._timeout == pytest.approx(4.5)

    def test_nth_does_not_mutate_original(self):
        loc = Locator(_window(), title="OK")
        loc.nth(3)
        assert "found_index" not in loc._criteria


# Auto-wait without explicit wait_for


class TestAutoWaitRetry:
    def test_click_succeeds_when_element_appears_after_delay(self):
        """click() waits automatically without an explicit wait_for()."""
        ready = threading.Event()
        spec = MagicMock()
        child = MagicMock()

        visible = MagicMock()
        visible.is_visible.return_value = True

        def appears_once_ready():
            if not ready.is_set():
                raise RuntimeError("element is not there yet")
            return visible

        child.wrapper_object.side_effect = appears_once_ready
        spec.child_window.return_value = child
        spec.children.return_value = []

        threading.Timer(0.3, ready.set).start()

        loc = _window(spec).get_by_role("Button").timeout(2.0)
        loc.click()  # no explicit wait_for; auto-wait handles the delay

        child.click_input.assert_called_once()

    def test_locator_timeout_bounds_the_visibility_wait(self):
        """The locator's own timeout governs how long _resolve waits.

        Asserted against the clock rather than a recorded call argument:
        the wait is our own poll loop now, so honouring the timeout is the
        only externally visible part of the contract.
        """
        loc = _window(_failing_spec()).get_by_role("Button").timeout(0.3)

        start = time.monotonic()
        with pytest.raises(ElementNotFoundError):
            loc.click()
        elapsed = time.monotonic() - start

        # Lower bound: it really waited. Upper bound: it used 0.3, not the
        # 4.0 s default that would apply if the timeout were dropped.
        assert 0.3 <= elapsed < 2.0

    def test_chained_actions_without_explicit_wait(self):
        """Chain of actions uses auto-wait on every step."""
        spec = _succeeding_spec()
        win = _window(spec)
        win.get_by_role("Edit").timeout(1.0).type_text("hello")
        win.get_by_role("Button").timeout(1.0).click()

        child = spec.child_window.return_value
        assert child.type_keys.called
        assert child.click_input.called


# ElementNotFoundError message format


class TestElementNotFoundError:
    def test_raises_element_not_found_error(self):
        loc = _window(_failing_spec()).get_by_role("Button").timeout(0.05)
        with pytest.raises(ElementNotFoundError):
            loc.click()

    def test_error_message_includes_waited_seconds(self):
        loc = _window(_failing_spec()).get_by_role("Button").timeout(0.05)
        with pytest.raises(ElementNotFoundError) as exc_info:
            loc.click()
        assert "0.05s" in str(exc_info.value)

    def test_error_message_includes_last_seen_tree(self):
        loc = _window(_failing_spec()).get_by_role("Button").timeout(0.05)
        with pytest.raises(ElementNotFoundError) as exc_info:
            loc.click()
        assert "last seen tree" in str(exc_info.value).lower()

    def test_error_message_includes_criteria(self):
        loc = _window(_failing_spec()).get_by_role("Button").timeout(0.05)
        with pytest.raises(ElementNotFoundError) as exc_info:
            loc.click()
        assert "Button" in str(exc_info.value)

    def test_element_not_found_is_dolphin_error(self):
        from dolphin_desktop import DolphinError

        assert issubclass(ElementNotFoundError, DolphinError)

    def test_double_click_raises_on_missing_element(self):
        loc = _window(_failing_spec()).get_by_role("Button").timeout(0.05)
        with pytest.raises(ElementNotFoundError):
            loc.double_click()

    def test_type_text_raises_on_missing_element(self):
        loc = _window(_failing_spec()).get_by_role("Edit").timeout(0.05)
        with pytest.raises(ElementNotFoundError):
            loc.type_text("hello")

    def test_timeout_value_in_error_matches_locator_timeout(self):
        loc = _window(_failing_spec()).get_by_role("Button").timeout(1.23)
        with pytest.raises(ElementNotFoundError) as exc_info:
            loc.click()
        assert "1.23s" in str(exc_info.value)


# The diagnostic tree is built only when somebody reads the message


class TestNotFoundTreeIsLazy:
    def test_tree_is_walked_only_when_the_message_is_read(self):
        spec = _failing_spec()
        loc = _window(spec).get_by_role("Button").timeout(0)
        with pytest.raises(ElementNotFoundError) as exc_info:
            loc._resolve()
        assert spec.children.call_count == 0
        assert "last seen tree" in str(exc_info.value).lower()
        assert spec.children.call_count == 1

    def test_polling_is_visible_never_walks_the_tree(self):
        """wait_until_hidden polls ~100 times — none of them may pay for a tree."""
        spec = _failing_spec()
        loc = _window(spec).get_by_role("Button")
        for _ in range(5):
            assert loc.is_visible() is False
        assert spec.children.call_count == 0


# A zero timeout still means "try once", not "never try"


class TestZeroTimeoutTriesOnce:
    def test_qt_object_name_exists_with_the_default_zero_timeout(self):
        spec = MagicMock()
        match = MagicMock()
        match.element_info.automation_id = "App.win.btn_ok"
        spec.descendants.return_value = [match]
        assert _window(spec).get_by_object_name("btn_ok").exists() is True

    def test_wait_until_hidden_returns_for_an_absent_element(self):
        loc = _window(_failing_spec()).get_by_role("Button")
        assert loc.wait_until_hidden(timeout=0) is loc

    def test_wait_for_checked_reads_the_state_once(self):
        spec = _succeeding_spec()
        spec.child_window.return_value.get_check_state.return_value = 1
        loc = _window(spec).get_by_role("CheckBox")
        assert loc.wait_for_checked(checked=True, timeout=0) is loc

    def test_wait_for_text_reads_the_text_once(self):
        spec = _succeeding_spec()
        spec.child_window.return_value.window_text.return_value = "Signed in"
        loc = _window(spec).get_by_role("Text")
        assert loc.wait_for_text("Signed", timeout=0) is loc


# wait_for resolves through the same path as every action


class TestWaitForUsesFallbacks:
    def _spec_where_only_the_fallback_resolves(self) -> MagicMock:
        primary = MagicMock()
        primary.wrapper_object.side_effect = Exception("primary never becomes visible")
        fallback = MagicMock()
        spec = MagicMock()
        spec.children.return_value = []
        spec.child_window.side_effect = lambda **kw: (
            fallback if kw == {"auto_id": "btnOk"} else primary
        )
        return spec

    def test_fallback_selector_satisfies_wait_for(self):
        spec = self._spec_where_only_the_fallback_resolves()
        loc = Locator(_window(spec), title="OK", fallback=[{"auto_id": "btnOk"}]).timeout(0.05)
        assert loc.wait_for() is loc

    def test_missing_element_raises_the_same_error_as_click(self):
        loc = _window(_failing_spec()).get_by_role("Button").timeout(0.05)
        with pytest.raises(ElementNotFoundError):
            loc.wait_for()

    def test_state_that_is_never_reached_still_raises_wait_timeout(self):
        from dolphin_desktop import WaitTimeoutError

        spec = _succeeding_spec()
        # _resolve no longer goes through wait(), so the only wait() left to
        # fail is the explicit state wait wait_for(state=...) performs.
        spec.child_window.return_value.wait.side_effect = Exception("never enabled")
        loc = _window(spec).get_by_role("Button").timeout(0.05)
        with pytest.raises(WaitTimeoutError):
            loc.wait_for(state="enabled")

    def test_hidden_state_polls_visibility_and_returns_same_locator(self):
        loc = _window().get_by_role("Button")
        visible = MagicMock(side_effect=[True, False])

        with (
            patch.object(loc, "is_visible", visible),
            patch.object(locator_module.time, "sleep") as sleep,
        ):
            assert loc.wait_for(state="hidden", timeout=1.0) is loc

        assert visible.call_count == 2
        sleep.assert_called_once_with(0.1)

    def test_hidden_state_times_out_when_element_stays_visible(self):
        from dolphin_desktop import WaitTimeoutError

        loc = _window().get_by_role("Button")
        with (
            patch.object(loc, "is_visible", return_value=True),
            patch.object(locator_module.time, "monotonic", side_effect=[0.0, 1.0]),
            patch.object(locator_module.time, "sleep"),
            pytest.raises(WaitTimeoutError, match="still visible"),
        ):
            loc.wait_for(state="hidden", timeout=0.5)

    def test_exists_reports_an_existing_hidden_element(self):
        spec = _succeeding_spec()
        spec.child_window.return_value.wrapper_object.return_value.is_visible.return_value = (
            False
        )
        loc = _window(spec).get_by_role("Button")

        assert loc.exists() is True

    def test_wait_for_exists_accepts_an_existing_hidden_element(self):
        spec = _succeeding_spec()
        spec.child_window.return_value.wrapper_object.return_value.is_visible.return_value = (
            False
        )
        loc = _window(spec).get_by_role("Button")

        assert loc.wait_for(state="exists", timeout=0.1) is loc

    def test_resolved_locator_exists_when_wrapped_element_is_hidden(self):
        element = MagicMock()
        element.is_visible.return_value = False
        loc = _ResolvedLocator(element)

        assert loc.exists() is True
        assert loc.wait_for(state="exists", timeout=0) is loc


# py.typed consumers must be able to chain through timeout()/nth()


class TestChainableTypingContract:
    @pytest.mark.parametrize("method", ["timeout", "nth", "_clone"])
    def test_clone_helpers_return_self_not_the_base_class(self, method):
        from typing import Self, get_type_hints

        hints = get_type_hints(getattr(Locator, method))
        assert hints["return"] is Self

    def test_specialized_control_methods_stay_reachable_after_timeout(self):
        spec = _succeeding_spec()
        win = _window(spec)
        # Both of these fail type checking when timeout() is annotated -> Locator.
        win.tab().timeout(2).select_tab("General")
        win.combo_box().timeout(2).selected_item()
        assert spec.child_window.return_value.selected_item.called
