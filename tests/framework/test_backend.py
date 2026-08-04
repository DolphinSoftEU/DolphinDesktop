"""Unit tests for the plug-in backend architecture."""

from __future__ import annotations

import argparse
import sys
from unittest.mock import MagicMock, patch

import pytest

import dolphin_desktop._backend as _bmod
from dolphin_desktop._backend import (
    _BUILT_IN,
    _REGISTRY,
    Backend,
    CDPBackend,
    ImageBackend,
    LinuxATSPIBackend,
    MacOSAccessibilityBackend,
    UIABackend,
    Win32Backend,
    list_backends,
    register,
    resolve,
)

# Helpers


def _make_backend(id_: str = "_tmp", platform_: str = "any") -> type[Backend]:
    """Return a minimal concrete Backend subclass."""

    class _B(Backend):
        id = id_
        platform = platform_

        def find_element(self, parent, criteria):
            return None

        def click(self, element, *, button="left"):
            pass

        def type_text(self, element, text):
            pass

        def get_tree(self, root, *, depth=None):
            return {}

        def screenshot(self, element=None):
            return None

    _B.__name__ = f"_Backend_{id_}"
    return _B


# Abstract base class


class TestBackendAbstractClass:
    def test_cannot_instantiate_backend_directly(self):
        with pytest.raises(TypeError):
            Backend()  # type: ignore[abstract]

    def test_minimal_concrete_subclass_instantiates(self):
        cls = _make_backend("_minimal_abc")
        b = cls()
        assert isinstance(b, Backend)

    def test_missing_abstract_method_prevents_instantiation(self):
        class _Incomplete(Backend):
            id = "_incomplete"
            platform = "any"

            def find_element(self, parent, criteria):
                return None

            def click(self, element, *, button="left"):
                pass

            def type_text(self, element, text):
                pass

            # get_tree and screenshot are intentionally missing

        with pytest.raises(TypeError):
            _Incomplete()  # type: ignore[abstract]

    def test_is_available_defaults_to_true(self):
        cls = _make_backend("_avail_default")
        assert cls().is_available() is True

    def test_description_extracts_docstring_first_line(self):
        b = UIABackend()
        desc = b.description()
        assert desc
        assert "\n" not in desc

    def test_repr_contains_class_name_and_id(self):
        b = UIABackend()
        r = repr(b)
        assert "UIABackend" in r
        assert "uia" in r


# Built-in backends


class TestUIABackend:
    def test_id_and_platform(self):
        assert UIABackend.id == "uia"
        assert UIABackend.platform == "windows"

    def test_is_available_on_windows(self):
        b = UIABackend()
        expected = sys.platform == "win32"
        assert b.is_available() is expected

    def test_find_element_delegates_to_child_window(self):
        b = UIABackend()
        parent = MagicMock()
        criteria = {"control_type": "Button", "title": "OK"}
        b.find_element(parent, criteria)
        parent.child_window.assert_called_once_with(**criteria)

    def test_click_left_calls_click_input(self):
        b = UIABackend()
        el = MagicMock()
        b.click(el)
        el.click_input.assert_called_once()

    def test_click_right_calls_right_click_input(self):
        b = UIABackend()
        el = MagicMock()
        b.click(el, button="right")
        el.right_click_input.assert_called_once()

    def test_click_middle_passes_button_arg(self):
        b = UIABackend()
        el = MagicMock()
        b.click(el, button="middle")
        el.click_input.assert_called_once_with(button="middle")

    def test_type_text_uses_type_keys_with_spaces(self):
        b = UIABackend()
        el = MagicMock()
        b.type_text(el, "hello world")
        # All three whitespace flags, not just with_spaces: parse_keys drops
        # a literal tab or newline when its flag is off, so typed text lost
        # them silently and the call still reported success.
        el.type_keys.assert_called_once_with(
            "hello world", with_spaces=True, with_tabs=True, with_newlines=True
        )

    def test_screenshot_element_calls_capture_as_image(self):
        b = UIABackend()
        el = MagicMock()
        b.screenshot(el)
        el.capture_as_image.assert_called_once()

    def test_get_tree_returns_dict_with_required_keys(self):
        b = UIABackend()
        root = MagicMock()
        root.element_info.name = "root"
        root.element_info.control_type = "Window"
        root.element_info.class_name = "Notepad"
        root.children.return_value = []
        tree = b.get_tree(root)
        assert "name" in tree
        assert "role" in tree
        assert "children" in tree


class TestWin32Backend:
    def test_id_and_platform(self):
        assert Win32Backend.id == "win32"
        assert Win32Backend.platform == "windows"

    def test_find_element_delegates_to_child_window(self):
        b = Win32Backend()
        parent = MagicMock()
        b.find_element(parent, {"title": "foo"})
        parent.child_window.assert_called_once_with(title="foo")

    def test_click_right(self):
        b = Win32Backend()
        el = MagicMock()
        b.click(el, button="right")
        el.right_click_input.assert_called_once()

    def test_type_text_uses_type_keys(self):
        b = Win32Backend()
        el = MagicMock()
        b.type_text(el, "win32 text")
        el.type_keys.assert_called_once_with(
            "win32 text", with_spaces=True, with_tabs=True, with_newlines=True
        )

    def test_screenshot_element(self):
        b = Win32Backend()
        el = MagicMock()
        b.screenshot(el)
        el.capture_as_image.assert_called_once()

    def test_get_tree_returns_dict_with_required_keys(self):
        b = Win32Backend()
        root = MagicMock()
        root.window_text.return_value = "root"
        root.friendly_class_name.return_value = "Window"
        root.class_name.return_value = "Notepad"
        root.children.return_value = []
        tree = b.get_tree(root)
        assert "name" in tree
        assert "role" in tree
        assert "children" in tree


class TestImageBackend:
    def test_id_and_platform(self):
        assert ImageBackend.id == "image"
        assert ImageBackend.platform == "any"

    def test_find_element_raises_without_template(self):
        b = ImageBackend()
        with pytest.raises(ValueError, match="template"):
            b.find_element(None, {})

    def test_get_tree_raises_capability_error(self):
        """ImageBackend does not publish GET_TREE.

        Pixel matching cannot walk an accessibility tree.
        """
        from dolphin_desktop import UnsupportedCapabilityError

        b = ImageBackend()
        with pytest.raises(UnsupportedCapabilityError):
            b.get_tree(None)

    def test_is_available_returns_bool(self):
        result = ImageBackend().is_available()
        assert isinstance(result, bool)

    def test_click_delegates_to_element_click(self):
        b = ImageBackend()
        el = MagicMock()
        b.click(el)
        el.click.assert_called_once()


# Stub backends are isolated from platform backends


STUB_CLASSES = [MacOSAccessibilityBackend, LinuxATSPIBackend, CDPBackend]


class TestStubBackends:
    @pytest.mark.parametrize("cls", STUB_CLASSES)
    def test_stub_is_available_answers_a_bool(self, cls):
        """``is_available()`` returns a bool rather than raising.

        Says nothing about which bool: ``macos``/``linux`` report False
        while ``cdp`` reports True (see
        ``test_reserved_platform_stubs_not_available``).
        """
        result = cls().is_available()
        assert isinstance(result, bool)

    @pytest.mark.parametrize("cls", STUB_CLASSES)
    def test_stub_find_element_raises_capability_error(self, cls):
        from dolphin_desktop import UnsupportedCapabilityError

        with pytest.raises(UnsupportedCapabilityError):
            cls().find_element(None, {})

    @pytest.mark.parametrize("cls", STUB_CLASSES)
    def test_stub_click_raises_capability_error(self, cls):
        from dolphin_desktop import UnsupportedCapabilityError

        with pytest.raises(UnsupportedCapabilityError):
            cls().click(None)

    @pytest.mark.parametrize("cls", STUB_CLASSES)
    def test_stub_type_text_raises_capability_error(self, cls):
        from dolphin_desktop import UnsupportedCapabilityError

        with pytest.raises(UnsupportedCapabilityError):
            cls().type_text(None, "")

    @pytest.mark.parametrize("cls", STUB_CLASSES)
    def test_stub_get_tree_raises_capability_error(self, cls):
        from dolphin_desktop import UnsupportedCapabilityError

        with pytest.raises(UnsupportedCapabilityError):
            cls().get_tree(None)

    @pytest.mark.parametrize("cls", STUB_CLASSES)
    def test_stub_screenshot_raises_capability_error(self, cls):
        from dolphin_desktop import UnsupportedCapabilityError

        with pytest.raises(UnsupportedCapabilityError):
            cls().screenshot()

    def test_patching_stub_does_not_affect_uia_find_element(self):
        """Changing MacOSAccessibilityBackend.find_element must not affect UIABackend."""
        uia = UIABackend()
        with patch.object(MacOSAccessibilityBackend, "find_element", return_value="patched"):
            parent = MagicMock()
            uia.find_element(parent, {"control_type": "Button"})
            # UIABackend still calls child_window, not the patched stub method
            parent.child_window.assert_called_once_with(control_type="Button")

    def test_patching_stub_does_not_affect_win32_find_element(self):
        win32 = Win32Backend()
        with patch.object(LinuxATSPIBackend, "find_element", return_value="patched"):
            parent = MagicMock()
            win32.find_element(parent, {"title": "X"})
            parent.child_window.assert_called_once_with(title="X")

    def test_stub_ids_are_distinct_from_mvp_ids(self):
        mvp_ids = {UIABackend.id, Win32Backend.id, ImageBackend.id}
        stub_ids = {MacOSAccessibilityBackend.id, LinuxATSPIBackend.id, CDPBackend.id}
        assert mvp_ids.isdisjoint(stub_ids)

    def test_stub_classes_are_distinct_from_mvp_classes(self):
        """The stub classes and the MVP backend classes are six distinct types.

        Guards against a stub being aliased onto a concrete backend (which
        would make the shared ``STUB_CLASSES`` suite silently exercise the
        real implementation). Both classes are in ``_BUILT_IN``; membership
        is covered by ``TestRegistry``.
        """
        mvp = {UIABackend, Win32Backend, ImageBackend}
        stubs = {MacOSAccessibilityBackend, LinuxATSPIBackend, CDPBackend}
        assert mvp.isdisjoint(stubs)


# Registry and register()


class TestRegistry:
    def test_all_builtin_ids_present_at_module_load(self):
        expected = {"uia", "win32", "qt", "image", "macos", "linux", "cdp"}
        assert expected <= set(_REGISTRY.keys())

    def test_builtin_list_contains_expected_backends(self):
        # 11 = 7 platform backends (UIA, Win32, Qt, Image, macOS, Linux, CDP)
        # + 4 marker backends (Delphi, Mainframe, Sap, Java). If this count
        # changes, update __all__ export list together.
        assert len(_BUILT_IN) == 11

    def test_register_as_decorator_adds_to_registry(self):
        @register
        class _TestDeco(Backend):
            id = "_test_deco"
            platform = "any"

            def find_element(self, parent, criteria):
                return None

            def click(self, element, *, button="left"):
                pass

            def type_text(self, element, text):
                pass

            def get_tree(self, root, *, depth=None):
                return {}

            def screenshot(self, element=None):
                return None

        try:
            assert "_test_deco" in _REGISTRY
            assert _REGISTRY["_test_deco"] is _TestDeco
        finally:
            _REGISTRY.pop("_test_deco", None)

    def test_register_returns_class_unchanged(self):
        cls = _make_backend("_test_ret")
        returned = register(cls)
        try:
            assert returned is cls
        finally:
            _REGISTRY.pop("_test_ret", None)

    def test_register_overwrites_existing_entry_with_warning(self):
        # Registering a different class under an existing id emits a
        # RuntimeWarning; overwrite still happens (last-in wins).
        cls_a = _make_backend("_test_ow")
        cls_b = _make_backend("_test_ow")
        register(cls_a)
        try:
            with pytest.warns(RuntimeWarning, match="already registered"):
                register(cls_b)
            assert _REGISTRY["_test_ow"] is cls_b
        finally:
            _REGISTRY.pop("_test_ow", None)


# resolve()


class TestResolve:
    def test_resolve_uia(self):
        assert isinstance(resolve("uia"), UIABackend)

    def test_resolve_win32(self):
        assert isinstance(resolve("win32"), Win32Backend)

    def test_resolve_image(self):
        assert isinstance(resolve("image"), ImageBackend)

    def test_resolve_macos(self):
        assert isinstance(resolve("macos"), MacOSAccessibilityBackend)

    def test_resolve_linux(self):
        assert isinstance(resolve("linux"), LinuxATSPIBackend)

    def test_resolve_cdp(self):
        assert isinstance(resolve("cdp"), CDPBackend)

    def test_resolve_unknown_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            resolve("_no_such_backend_xyz")

    def test_resolve_unknown_error_names_available_backends(self):
        with pytest.raises(ValueError, match="uia"):
            resolve("_no_such_backend_xyz")

    def test_resolve_auto_on_windows_returns_uia(self):
        if sys.platform == "win32":
            assert isinstance(resolve("auto"), UIABackend)

    def test_resolve_auto_darwin_returns_macos_stub(self):
        with patch.object(_bmod, "sys") as mock_sys:
            mock_sys.platform = "darwin"
            b = _bmod._auto_detect()
        assert isinstance(b, MacOSAccessibilityBackend)

    def test_resolve_auto_linux_returns_linux_stub(self):
        with patch.object(_bmod, "sys") as mock_sys:
            mock_sys.platform = "linux"
            # use _auto_detect directly so _load_plugins doesn't re-run
            b = _bmod._auto_detect()
        assert isinstance(b, LinuxATSPIBackend)

    def test_resolve_auto_unknown_platform_falls_back_to_image(self):
        with patch.object(_bmod, "sys") as mock_sys:
            mock_sys.platform = "freebsd"
            b = _bmod._auto_detect()
        assert isinstance(b, ImageBackend)

    def test_resolve_registered_plugin_backend(self):
        cls = _make_backend("_test_plugin")
        register(cls)
        try:
            b = resolve("_test_plugin")
            assert isinstance(b, cls)
        finally:
            _REGISTRY.pop("_test_plugin", None)

    def test_each_resolve_returns_new_instance(self):
        b1 = resolve("uia")
        b2 = resolve("uia")
        assert b1 is not b2


# list_backends()


class TestListBackends:
    def test_returns_list_of_dicts(self):
        result = list_backends()
        assert isinstance(result, list)
        assert all(isinstance(x, dict) for x in result)

    def test_contains_all_builtin_ids(self):
        ids = {b["id"] for b in list_backends()}
        expected = {"uia", "win32", "image", "macos", "linux", "cdp"}
        assert expected <= ids

    def test_each_entry_has_required_keys(self):
        required = {"id", "platform", "class", "available", "description", "source"}
        for b in list_backends():
            assert required <= b.keys(), f"Entry {b['id']!r} missing keys"

    def test_available_is_bool(self):
        for b in list_backends():
            assert isinstance(b["available"], bool), f"Entry {b['id']!r}: 'available' not bool"

    def test_builtin_source_label(self):
        by_id = {b["id"]: b for b in list_backends()}
        for bid in ("uia", "win32", "image", "macos", "linux", "cdp"):
            assert by_id[bid]["source"] == "built-in"

    def test_uia_available_on_windows(self):
        if sys.platform == "win32":
            by_id = {b["id"]: b for b in list_backends()}
            assert by_id["uia"]["available"] is True

    def test_reserved_platform_stubs_not_available(self):
        # macOS/Linux are reserved stubs — is_available reports False.
        # CDP fronts the cross-platform CDPSession stack — available=True.
        by_id = {b["id"]: b for b in list_backends()}
        assert by_id["macos"]["available"] is False
        assert by_id["linux"]["available"] is False
        assert by_id["cdp"]["available"] is True

    def test_registered_plugin_appears_with_plugin_source(self):
        cls = _make_backend("_test_lst")
        register(cls)
        try:
            by_id = {b["id"]: b for b in list_backends()}
            assert "_test_lst" in by_id
            assert by_id["_test_lst"]["source"] == "plugin"
        finally:
            _REGISTRY.pop("_test_lst", None)

    def test_class_field_is_fully_qualified(self):
        by_id = {b["id"]: b for b in list_backends()}
        assert "dolphin_desktop._backend.UIABackend" == by_id["uia"]["class"]

    def test_description_is_string(self):
        for b in list_backends():
            assert isinstance(b["description"], str)


# dolphin info backends CLI


class TestInfoBackendsCLI:
    def _run(self, capsys):
        from dolphin_desktop._cli import _info_backends_cmd

        _info_backends_cmd(argparse.Namespace())
        return capsys.readouterr().out

    def test_output_contains_all_builtin_ids(self, capsys):
        out = self._run(capsys)
        for bid in ("uia", "win32", "image", "macos", "linux", "cdp"):
            assert bid in out, f"Expected '{bid}' in output"

    def test_output_contains_available_marker(self, capsys):
        out = self._run(capsys)
        assert "yes" in out

    def test_output_contains_total_line(self, capsys):
        out = self._run(capsys)
        assert "Total:" in out

    def test_output_mentions_entry_point_group(self, capsys):
        out = self._run(capsys)
        assert "dolphin_desktop.backends" in out

    def test_output_has_header_row(self, capsys):
        out = self._run(capsys)
        assert "ID" in out
        assert "PLATFORM" in out
        assert "AVAIL" in out

    def test_total_count_matches_list_backends(self, capsys):
        out = self._run(capsys)
        n = len(list_backends())
        assert f"Total: {n}" in out


# Documentation


class TestDocumentation:
    def test_architecture_md_exists(self):
        from pathlib import Path

        arch = Path(__file__).parent.parent.parent / "docs" / "architecture.md"
        assert arch.exists(), "docs/architecture.md not found"

    def test_architecture_md_has_layer_diagram(self):
        from pathlib import Path

        content = (Path(__file__).parent.parent.parent / "docs" / "architecture.md").read_text(
            encoding="utf-8"
        )
        assert "┌" in content or "```" in content, "No diagram found in docs/architecture.md"

    def test_architecture_md_mentions_plugin_model(self):
        from pathlib import Path

        content = (Path(__file__).parent.parent.parent / "docs" / "architecture.md").read_text(
            encoding="utf-8"
        )
        assert "entry-point" in content or "entry_point" in content or "entry point" in content

    def test_architecture_md_references_correct_module_name(self):
        from pathlib import Path

        content = (Path(__file__).parent.parent.parent / "docs" / "architecture.md").read_text(
            encoding="utf-8"
        )
        assert "dolphin_desktop" in content, (
            "docs/architecture.md should reference 'dolphin_desktop', not just 'dolphin'"
        )


# Public API re-exports


class TestPublicAPIExports:
    def test_backend_class_exported(self):
        import dolphin_desktop as d

        assert d.Backend is Backend

    def test_mvp_backends_exported(self):
        import dolphin_desktop as d

        assert d.UIABackend is UIABackend
        assert d.Win32Backend is Win32Backend
        assert d.ImageBackend is ImageBackend

    def test_stub_backends_exported(self):
        import dolphin_desktop as d

        assert d.MacOSAccessibilityBackend is MacOSAccessibilityBackend
        assert d.LinuxATSPIBackend is LinuxATSPIBackend
        assert d.CDPBackend is CDPBackend

    def test_list_backends_exported(self):
        import dolphin_desktop as d

        assert d.list_backends is list_backends

    def test_register_backend_exported(self):
        import dolphin_desktop as d
        from dolphin_desktop._backend import register as _register

        assert d.register_backend is _register

    def test_resolve_backend_exported(self):
        import dolphin_desktop as d
        from dolphin_desktop._backend import resolve as _resolve

        assert d.resolve_backend is _resolve
