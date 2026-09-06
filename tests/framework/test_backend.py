"""Unit tests for the plug-in backend architecture."""


# Helpers

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import sys
import warnings
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

import dolphin_desktop._backend as _bmod
import dolphin_desktop._backend as backend
from dolphin_desktop._backend import (
    _BUILT_IN,
    _REGISTRY,
    Backend,
    CDPBackend,
    DelphiBackend,
    ImageBackend,
    JavaBackend,
    LinuxATSPIBackend,
    MacOSAccessibilityBackend,
    MainframeBackend,
    QtBackend,
    SapBackend,
    UIABackend,
    Win32Backend,
    list_backends,
    register,
    resolve,
    supported_backends,
)
from dolphin_desktop._capabilities import IMAGE_ONLY, STANDARD_ACCESSIBILITY, Capability
from dolphin_desktop._exceptions import UnsupportedCapabilityError


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
        expected = {
            "uia",
            "win32",
            "qt",
            "image",
            "macos",
            "linux",
            "cdp",
            "delphi",
            "mainframe",
            "sap",
            "java",
        }
        assert {backend.id for backend in _BUILT_IN} == expected

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


def test_backend_constructor_and_capability_argument_validation() -> None:
    from dolphin_desktop._backend import Backend, ImageBackend

    assert repr(ImageBackend()) == "ImageBackend(id='image')"
    with pytest.raises(TypeError, match="Capability"):
        ImageBackend().supports("click")  # type: ignore[arg-type]

    class MissingId(Backend):
        platform = "any"

        find_element = click = type_text = get_tree = screenshot = lambda *args, **kwargs: None

    with pytest.raises(TypeError, match="must set a class attribute"):
        MissingId()


def _backend_class(backend_id: str = "_completeness", *, caps=()):
    """Build a concrete backend with a deliberately configurable contract."""

    class TestBackend(backend.Backend):
        id = backend_id
        platform = "any"

        def find_element(self, parent, criteria):
            return None

        def click(self, element, *, button="left"):
            return None

        def type_text(self, element, text):
            return None

        def get_tree(self, root, *, depth=None):
            return {}

        def screenshot(self, element=None):
            return None

        def capabilities(self):
            return caps

    TestBackend.__name__ = f"Backend_{backend_id or 'invalid'}"
    return TestBackend


def _additional_backend_class(backend_module, backend_id: str, *, capabilities=()):
    class TestBackend(backend_module.Backend):
        id = backend_id
        platform = "any"

        def find_element(self, parent, criteria):
            return None

        def click(self, element, *, button="left"):
            return None

        def type_text(self, element, text):
            return None

        def get_tree(self, root, *, depth=None):
            return {}

        def screenshot(self, element=None):
            return None

        def capabilities(self):
            return capabilities

    TestBackend.__name__ = f"Backend_{backend_id or 'invalid'}"
    return TestBackend


def _node(name="root", role="Window", class_name="App", children=()):
    info = SimpleNamespace(name=name, control_type=role, class_name=class_name)
    return SimpleNamespace(element_info=info, children=lambda: list(children))


def _win32_node(name="root", role="Window", class_name="App", children=()):
    return SimpleNamespace(
        window_text=lambda: name,
        friendly_class_name=lambda: role,
        class_name=lambda: class_name,
        children=lambda: list(children),
    )


class TestCapabilityAndBaseContract:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("invoke", Capability.INVOKE), ("INVOKE", Capability.INVOKE)],
    )
    def test_capability_string_hints_the_matching_member(self, value, expected):
        with pytest.raises(TypeError, match=f"Capability\\.{expected.name}"):
            backend._check_capability_arg(value, "unit")

    def test_unknown_string_and_arbitrary_object_get_clear_type_errors(self):
        with pytest.raises(TypeError, match="got str"):
            backend._check_capability_arg("not-a-capability", "unit")
        with pytest.raises(TypeError, match="list"):
            backend._check_capability_arg([], "unit")

    @pytest.mark.parametrize("attribute", ["id", "platform"])
    @pytest.mark.parametrize("bad_value", [None, "", 123])
    def test_backend_constructor_validates_class_metadata(self, attribute, bad_value):
        cls = _backend_class(f"_bad_{attribute}_{bad_value!r}")
        setattr(cls, attribute, bad_value)
        with pytest.raises(TypeError, match=attribute):
            cls()

    def test_default_base_helpers(self):
        cls = _backend_class("_base_defaults")
        delattr(cls, "capabilities")
        instance = cls()
        assert instance.is_available() is True
        assert instance.description() == ""
        assert instance.capabilities() == frozenset()
        assert instance.supports(Capability.CLICK) is False
        assert repr(instance) == "Backend__base_defaults(id='_base_defaults')"

    def test_supports_accepts_member_and_rejects_none_capabilities(self):
        cls = _backend_class("_supports", caps=frozenset({Capability.CLICK}))
        instance = cls()
        assert instance.supports(Capability.CLICK) is True
        assert instance.supports(Capability.INVOKE) is False

        none_cls = _backend_class("_none_caps")
        none_cls.capabilities = lambda self: None
        with pytest.raises(TypeError, match="returned None"):
            none_cls().supports(Capability.CLICK)

    def test_require_capability_returns_for_supported_operation(self):
        cls = _backend_class("_require_ok", caps=frozenset({Capability.CLICK}))
        cls().require_capability(Capability.CLICK)

    def test_require_capability_reports_alternative_and_excludes_self(self, monkeypatch):
        source = _backend_class("_source", caps=frozenset())
        alternative = _backend_class("_alternative", caps=frozenset({Capability.INVOKE}))
        monkeypatch.setattr(backend, "_REGISTRY", {source.id: source, alternative.id: alternative})
        monkeypatch.setattr(backend, "_load_plugins", lambda: None)

        with pytest.raises(UnsupportedCapabilityError) as exc_info:
            source().require_capability(Capability.INVOKE)
        message = str(exc_info.value)
        assert "_source" not in exc_info.value.hint
        assert "_alternative" in message
        assert "Capability.INVOKE" in message

    def test_require_capability_reports_no_alternative(self, monkeypatch):
        cls = _backend_class("_only_backend", caps=frozenset())
        monkeypatch.setattr(backend, "_REGISTRY", {cls.id: cls})
        monkeypatch.setattr(backend, "_load_plugins", lambda: None)

        with pytest.raises(UnsupportedCapabilityError) as exc_info:
            cls().require_capability(Capability.INVOKE)
        assert "no other registered backend" in exc_info.value.hint


class TestConcreteCapabilitiesAndOperations:
    @pytest.mark.parametrize(
        ("cls", "expected"),
        [
            (
                UIABackend,
                STANDARD_ACCESSIBILITY
                | {Capability.SCREENSHOT, Capability.DRAG, Capability.SCROLL},
            ),
            (
                Win32Backend,
                frozenset(
                    {
                        Capability.LOCATE,
                        Capability.GET_TREE,
                        Capability.READ_TEXT,
                        Capability.READ_STATE,
                        Capability.CLICK,
                        Capability.DOUBLE_CLICK,
                        Capability.RIGHT_CLICK,
                        Capability.HOVER,
                        Capability.DRAG,
                        Capability.TYPE_TEXT,
                        Capability.PRESS_KEY,
                        Capability.SCROLL,
                        Capability.SCREENSHOT,
                    }
                ),
            ),
            (ImageBackend, IMAGE_ONLY),
        ],
    )
    def test_concrete_capability_sets_are_declared(self, cls, expected):
        assert cls().capabilities() == expected

    def test_qt_backend_uses_uia_capabilities_and_custom_description(self):
        assert QtBackend().capabilities() == UIABackend().capabilities()
        assert QtBackend().description().startswith("Qt 5/6 backend")

    @pytest.mark.parametrize(
        ("backend_cls", "element_method", "button"),
        [
            (UIABackend, "click_input", "left"),
            (UIABackend, "right_click_input", "right"),
            (UIABackend, "click_input", "middle"),
            (Win32Backend, "click_input", "left"),
            (Win32Backend, "right_click_input", "right"),
            (Win32Backend, "click_input", "middle"),
        ],
    )
    def test_uia_and_win32_click_variants(self, backend_cls, element_method, button):
        element = MagicMock()
        backend_cls().click(element, button=button)
        if button == "middle":
            getattr(element, element_method).assert_called_once_with(button="middle")
        else:
            getattr(element, element_method).assert_called_once_with()

    def test_uia_and_win32_unknown_button_uses_standard_click(self):
        for backend_cls in (UIABackend, Win32Backend):
            element = MagicMock()
            backend_cls().click(element, button="not-a-special-button")
            element.click_input.assert_called_once_with()

    def test_uia_find_and_type_escape_text(self):
        instance = UIABackend()
        parent = MagicMock()
        assert instance.find_element(parent, {"title": "Save"}) is parent.child_window.return_value
        parent.child_window.assert_called_once_with(title="Save")
        element = MagicMock()
        instance.type_text(element, "literal {text} + ^ %")
        element.type_keys.assert_called_once_with(
            "literal {{}text{}} {+} {^} {%}",
            with_spaces=True,
            with_tabs=True,
            with_newlines=True,
        )

    def test_win32_find_and_type_escape_text(self):
        instance = Win32Backend()
        parent = MagicMock()
        instance.find_element(parent, {"title": "Save"})
        parent.child_window.assert_called_once_with(title="Save")
        element = MagicMock()
        instance.type_text(element, "literal {text}")
        element.type_keys.assert_called_once_with(
            "literal {{}text{}}", with_spaces=True, with_tabs=True, with_newlines=True
        )

    def test_uia_tree_walks_children_and_honours_depth(self):
        grandchild = _node("grandchild", "Button", "ButtonClass")
        child = _node("child", "Pane", "PaneClass", [grandchild])
        root = _node("root", "Window", "WindowClass", [child])
        instance = UIABackend()

        assert instance.get_tree(root) == {
            "name": "root",
            "role": "Window",
            "class": "WindowClass",
            "children": [
                {
                    "name": "child",
                    "role": "Pane",
                    "class": "PaneClass",
                    "children": [
                        {
                            "name": "grandchild",
                            "role": "Button",
                            "class": "ButtonClass",
                            "children": [],
                        }
                    ],
                }
            ],
        }
        assert instance.get_tree(root, depth=0)["children"] == []
        assert instance.get_tree(root, depth=1)["children"][0]["children"] == []

    def test_uia_tree_uses_fallback_for_bad_info_and_bad_children(self):
        class BadInfo:
            @property
            def element_info(self):
                raise RuntimeError("metadata unavailable")

        bad_info = BadInfo()
        assert UIABackend().get_tree(bad_info) == {
            "name": "",
            "role": "unknown",
            "class": "",
            "children": [],
        }

        bad_children = _node("root")
        bad_children.children = Mock(side_effect=RuntimeError("children unavailable"))
        assert UIABackend().get_tree(bad_children)["children"] == []

    def test_win32_tree_walks_children_and_handles_failures(self):
        child = _win32_node("child", "Button", "ButtonClass")
        root = _win32_node("root", "Window", "WindowClass", [child])
        instance = Win32Backend()
        assert instance.get_tree(root, depth=1)["children"][0]["name"] == "child"
        assert instance.get_tree(root, depth=0)["children"] == []

        bad_info = SimpleNamespace(
            window_text=Mock(side_effect=RuntimeError("metadata unavailable")),
            friendly_class_name=Mock(),
            class_name=Mock(),
            children=Mock(),
        )
        assert instance.get_tree(bad_info)["role"] == "unknown"
        bad_children = _win32_node("root")
        bad_children.children = Mock(side_effect=RuntimeError("children unavailable"))
        assert instance.get_tree(bad_children)["children"] == []

    @pytest.mark.parametrize("backend_cls", [UIABackend, Win32Backend])
    def test_screenshot_uses_element_or_all_monitors(self, backend_cls):
        instance = backend_cls()
        element = MagicMock()
        assert instance.screenshot(element) is element.capture_as_image.return_value
        element.capture_as_image.assert_called_once_with()
        with patch("PIL.ImageGrab.grab", return_value="desktop") as grab:
            assert instance.screenshot() == "desktop"
        grab.assert_called_once_with(all_screens=True)

    @pytest.mark.parametrize("platform_name, expected", [("win32", True), ("linux", False)])
    def test_uia_and_win32_availability_with_dependency(self, monkeypatch, platform_name, expected):
        monkeypatch.setitem(sys.modules, "pywinauto", ModuleType("pywinauto"))
        monkeypatch.setattr(sys, "platform", platform_name)
        assert UIABackend().is_available() is expected
        assert Win32Backend().is_available() is expected

    @pytest.mark.parametrize("backend_cls", [UIABackend, Win32Backend])
    def test_uia_and_win32_availability_without_dependency(self, monkeypatch, backend_cls):
        monkeypatch.setitem(sys.modules, "pywinauto", None)
        assert backend_cls().is_available() is False

    def test_image_find_click_type_and_screenshot(self, monkeypatch):
        instance = ImageBackend()
        with pytest.raises(ValueError, match="template"):
            instance.find_element(None, {})
        locator = MagicMock()
        with patch("dolphin_desktop._image.ImageLocator", return_value=locator) as locator_cls:
            assert (
                instance.find_element(None, {"template": "save.png", "threshold": 0.91}) is locator
            )
        locator_cls.assert_called_once_with("save.png", threshold=0.91)

        left = MagicMock()
        instance.click(left)
        left.click.assert_called_once_with()
        right = MagicMock()
        instance.click(right, button="right")
        right.right_click_input.assert_called_once_with()
        middle = MagicMock()
        instance.click(middle, button="middle")
        middle.click_input.assert_called_once_with()
        other = MagicMock()
        instance.click(other, button="unknown")
        other.click.assert_called_once_with()

        typed = MagicMock()
        keyboard = Mock()
        monkeypatch.setattr("dolphin_desktop._keyboard.Keyboard", keyboard)
        instance.type_text(typed, "hello")
        typed.click.assert_called_once_with()
        keyboard.type.assert_called_once_with("hello")

        with patch("dolphin_desktop._image.Screen.screenshot", return_value="screen") as shot:
            assert instance.screenshot() == "screen"
        shot.assert_called_once_with()

    @pytest.mark.parametrize("dependency", ["cv2"])
    def test_image_availability_without_optional_dependency(self, monkeypatch, dependency):
        monkeypatch.setitem(sys.modules, dependency, None)
        assert ImageBackend().is_available() is False

    def test_image_availability_with_optional_dependency(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "cv2", ModuleType("cv2"))
        assert ImageBackend().is_available() is True


class TestMarkerBackends:
    @pytest.mark.parametrize(
        ("cls", "expected"),
        [
            (MacOSAccessibilityBackend, frozenset()),
            (LinuxATSPIBackend, frozenset()),
            (
                DelphiBackend,
                STANDARD_ACCESSIBILITY
                | {Capability.SCREENSHOT, Capability.DRAG, Capability.SCROLL},
            ),
            (
                MainframeBackend,
                frozenset(
                    {
                        Capability.LOCATE,
                        Capability.READ_TEXT,
                        Capability.READ_STATE,
                        Capability.TYPE_TEXT,
                        Capability.PRESS_KEY,
                    }
                ),
            ),
            (
                CDPBackend,
                STANDARD_ACCESSIBILITY
                | {Capability.SCREENSHOT, Capability.DRAG, Capability.SCROLL},
            ),
            (
                SapBackend,
                frozenset(
                    {
                        Capability.LOCATE,
                        Capability.GET_TREE,
                        Capability.READ_TEXT,
                        Capability.READ_STATE,
                        Capability.CLICK,
                        Capability.DOUBLE_CLICK,
                        Capability.RIGHT_CLICK,
                        Capability.TYPE_TEXT,
                        Capability.PRESS_KEY,
                        Capability.INVOKE,
                        Capability.TOGGLE,
                        Capability.SELECT,
                        Capability.SET_VALUE,
                        Capability.SCREENSHOT,
                    }
                ),
            ),
            (JavaBackend, STANDARD_ACCESSIBILITY | {Capability.SCREENSHOT, Capability.SCROLL}),
        ],
    )
    def test_marker_capability_sets(self, cls, expected):
        assert cls().capabilities() == expected

    @pytest.mark.parametrize(
        "cls",
        [
            MacOSAccessibilityBackend,
            LinuxATSPIBackend,
            CDPBackend,
            DelphiBackend,
            MainframeBackend,
            SapBackend,
            JavaBackend,
        ],
    )
    def test_marker_methods_raise_actionable_error(self, cls):
        instance = cls()
        with pytest.raises(UnsupportedCapabilityError) as exc_info:
            instance.find_element(None, {})
        assert type(instance).__name__ in str(exc_info.value)
        assert "matching facade" in exc_info.value.hint

        with pytest.raises(UnsupportedCapabilityError):
            instance.click(None)
        with pytest.raises(UnsupportedCapabilityError):
            instance.type_text(None, "")
        with pytest.raises(UnsupportedCapabilityError):
            instance.get_tree(None)
        with pytest.raises(UnsupportedCapabilityError):
            instance.screenshot()

    def test_default_marker_availability_is_false(self):
        assert MacOSAccessibilityBackend().is_available() is False

    @pytest.mark.parametrize("cls", [CDPBackend, MainframeBackend])
    def test_cross_platform_markers_are_available(self, cls):
        assert cls().is_available() is True

    @pytest.mark.parametrize("cls", [DelphiBackend, SapBackend, JavaBackend])
    @pytest.mark.parametrize("platform_name, expected", [("win32", True), ("linux", False)])
    def test_windows_marker_availability(self, monkeypatch, cls, platform_name, expected):
        monkeypatch.setattr(sys, "platform", platform_name)
        assert cls().is_available() is expected


class _EntryPoint:
    def __init__(self, name, loaded=None, error=None):
        self.name = name
        self.loaded = loaded
        self.error = error

    def load(self):
        if self.error is not None:
            raise self.error
        return self.loaded


class TestRegistryAndPluginLoading:
    def test_load_plugins_fast_path_and_reentrant_lock_path(self, monkeypatch):
        loader = Mock()
        monkeypatch.setattr(backend, "_load_plugins_locked", loader)
        monkeypatch.setattr(backend, "_plugins_loaded", True)
        backend._load_plugins()
        loader.assert_not_called()

        class LockThatSeesAnotherLoader:
            def __enter__(self):
                backend._plugins_loaded = True
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr(backend, "_plugins_loaded", False)
        monkeypatch.setattr(backend, "_plugins_lock", LockThatSeesAnotherLoader())
        backend._load_plugins()
        loader.assert_not_called()

    def test_plugin_discovery_handles_entry_points_failure(self, monkeypatch):
        monkeypatch.setattr(
            importlib.metadata, "entry_points", Mock(side_effect=RuntimeError("metadata broken"))
        )
        backend._load_plugins_locked()

    def test_load_plugins_runs_discovery_once_when_not_loaded(self, monkeypatch):
        loader = Mock()
        monkeypatch.setattr(backend, "_load_plugins_locked", loader)
        monkeypatch.setattr(backend, "_plugins_loaded", False)
        backend._load_plugins()
        assert backend._plugins_loaded is True
        loader.assert_called_once_with()

    def test_plugin_discovery_registers_valid_plugins_and_skips_invalid_ones(self, monkeypatch):
        valid = _backend_class("_ep_valid")
        same = _backend_class("_ep_same")
        invalid_id = _backend_class("")
        collision = _backend_class("uia")
        registry = dict(backend._REGISTRY)
        registry[same.id] = same
        monkeypatch.setattr(backend, "_REGISTRY", registry)
        monkeypatch.setattr(
            importlib.metadata,
            "entry_points",
            lambda **kwargs: [
                _EntryPoint("broken", error=ImportError("dependency missing")),
                _EntryPoint("not-backend", loaded=object()),
                _EntryPoint("invalid-id", loaded=invalid_id),
                _EntryPoint(valid.id, loaded=valid),
                _EntryPoint(same.id, loaded=same),
                _EntryPoint("alias", loaded=_backend_class("_ep_alias")),
                _EntryPoint("replace-uia", loaded=collision),
            ],
        )

        with pytest.warns(RuntimeWarning) as caught:
            backend._load_plugins_locked()
        assert registry[valid.id] is valid
        assert registry[same.id] is same
        assert registry["uia"] is not collision
        messages = [str(w.message) for w in caught]
        assert any("failed to load" in message for message in messages)
        assert any("does not point at a Backend" in message for message in messages)
        assert any("not a non-empty str" in message for message in messages)
        assert any("does not match" in message for message in messages)
        assert any("tries to replace" in message for message in messages)

    @pytest.mark.parametrize("bad_value", [object(), lambda: None])
    def test_register_rejects_non_backend_objects(self, bad_value):
        with pytest.raises(TypeError, match="Backend subclass"):
            register(bad_value)

    def test_register_rejects_a_class_that_is_not_a_backend(self):
        class NotABackend:
            pass

        with pytest.raises(TypeError, match="Backend subclass"):
            register(NotABackend)

    @pytest.mark.parametrize("bad_id", [None, "", 0])
    def test_register_rejects_invalid_backend_ids(self, bad_id):
        cls = _backend_class(f"_register_bad_{bad_id!r}")
        cls.id = bad_id
        with pytest.raises(TypeError, match="non-empty"):
            register(cls)

    def test_register_same_class_is_idempotent(self):
        cls = _backend_class("_register_same")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            assert register(cls) is cls
            assert register(cls) is cls
        assert not caught
        assert backend._REGISTRY[cls.id] is cls
        backend._REGISTRY.pop(cls.id, None)

    def test_register_warns_and_replaces_different_class(self):
        old = _backend_class("_register_collision")
        new = _backend_class("_register_collision")
        backend._REGISTRY[old.id] = old
        try:
            with pytest.warns(RuntimeWarning, match="already registered"):
                register(new)
            assert backend._REGISTRY[new.id] is new
        finally:
            backend._REGISTRY.pop(old.id, None)


class TestResolveAndListing:
    @pytest.mark.parametrize(
        ("platform_name", "expected"),
        [
            ("win32", UIABackend),
            ("darwin", MacOSAccessibilityBackend),
            ("linux", LinuxATSPIBackend),
            ("linux-gnu", LinuxATSPIBackend),
            ("freebsd", ImageBackend),
        ],
    )
    def test_auto_detects_each_platform(self, monkeypatch, platform_name, expected):
        monkeypatch.setattr(sys, "platform", platform_name)
        assert isinstance(backend._auto_detect(), expected)

    def test_resolve_auto_delegates_to_auto_detect(self, monkeypatch):
        detected = object()
        monkeypatch.setattr(backend, "_load_plugins", Mock())
        monkeypatch.setattr(backend, "_auto_detect", Mock(return_value=detected))
        assert resolve("auto") is detected
        backend._auto_detect.assert_called_once_with()

    def test_resolve_wraps_constructor_error(self, monkeypatch):
        cls = _backend_class("_constructor_error")
        cls.__init__ = Mock(side_effect=RuntimeError("constructor failed"))
        monkeypatch.setattr(backend, "_REGISTRY", {cls.id: cls})
        monkeypatch.setattr(backend, "_load_plugins", lambda: None)
        with pytest.raises(ValueError, match="constructor_error") as exc_info:
            resolve(cls.id)
        assert "RuntimeError: constructor failed" in str(exc_info.value)

    def test_resolve_unknown_backend_names_available_ids(self, monkeypatch):
        cls = _backend_class("_known_backend")
        monkeypatch.setattr(backend, "_REGISTRY", {cls.id: cls})
        monkeypatch.setattr(backend, "_load_plugins", lambda: None)
        with pytest.raises(ValueError, match="Unknown backend") as exc_info:
            resolve("_missing_backend")
        assert "_known_backend" in str(exc_info.value)

    def test_list_backends_is_resilient_to_broken_plugins(self, monkeypatch):
        good = _backend_class("_listed_good", caps=[Capability.CLICK, "ignored"])

        class Unstable(_backend_class("_listed_unstable")):
            def is_available(self):
                raise RuntimeError("availability failed")

            def description(self):
                raise RuntimeError("description failed")

            def capabilities(self):
                raise RuntimeError("capability failed")

        broken = _backend_class("_listed_broken")
        broken.__init__ = Mock(side_effect=RuntimeError("init failed"))
        no_platform = _backend_class("_listed_no_platform")
        delattr(no_platform, "platform")
        no_platform.__init__ = Mock(side_effect=RuntimeError("init failed"))
        monkeypatch.setattr(
            backend,
            "_REGISTRY",
            {good.id: good, Unstable.id: Unstable, broken.id: broken, no_platform.id: no_platform},
        )
        monkeypatch.setattr(backend, "_load_plugins", lambda: None)

        rows = list_backends()
        by_id = {row["id"]: row for row in rows}
        assert by_id[good.id]["available"] is True
        assert by_id[good.id]["capabilities"] == ["click"]
        assert by_id[Unstable.id]["available"] is False
        assert by_id[Unstable.id]["description"] == ""
        assert by_id[Unstable.id]["capabilities"] == []
        assert by_id[broken.id]["available"] is False
        assert "instantiation failed" in by_id[broken.id]["description"]
        assert by_id[no_platform.id]["platform"] == "unknown"

    def test_safe_call_and_safe_capabilities_cover_all_bad_shapes(self):
        assert backend._safe_call(lambda: "ok", default="fallback") == "ok"
        assert (
            backend._safe_call(lambda: (_ for _ in ()).throw(RuntimeError()), default="fallback")
            == "fallback"
        )

        instance = _backend_class("_safe_shapes")()
        for raw in (None, 1):
            instance.capabilities = lambda raw=raw: raw
            assert backend._safe_capabilities(instance) == []
        instance.capabilities = lambda: [Capability.CLICK, "wrong"]
        assert backend._safe_capabilities(instance) == ["click"]
        instance.capabilities = Mock(side_effect=RuntimeError("broken"))
        assert backend._safe_capabilities(instance) == []

    def test_supported_backends_filters_bad_plugins_and_validates_argument(self, monkeypatch):
        good = _backend_class("_support_good", caps=frozenset({Capability.CLICK}))
        bad_caps = _backend_class("_support_bad_caps")
        bad_caps.capabilities = Mock(side_effect=RuntimeError("broken"))
        bad_init = _backend_class("_support_bad_init")
        bad_init.__init__ = Mock(side_effect=RuntimeError("broken"))
        excluded = _backend_class("_support_excluded", caps=frozenset({Capability.CLICK}))
        monkeypatch.setattr(
            backend,
            "_REGISTRY",
            {good.id: good, bad_caps.id: bad_caps, bad_init.id: bad_init, excluded.id: excluded},
        )
        monkeypatch.setattr(backend, "_load_plugins", lambda: None)
        assert supported_backends(Capability.CLICK) == [good.id, excluded.id]
        assert backend._find_supporting_backends(Capability.CLICK, exclude_id=excluded.id) == [
            good.id
        ]
        with pytest.raises(TypeError, match="Capability"):
            supported_backends("click")


def test_backend_registry_listing_and_auto_detection(monkeypatch) -> None:
    import dolphin_desktop._backend as backend
    from dolphin_desktop import Capability

    class GoodBackend(backend.Backend):
        id = "plugin-good"
        platform = "test"

        def capabilities(self):
            return frozenset({Capability.CLICK})

        def find_element(self, parent, criteria):
            return None

        def click(self, element, *, button="left"):
            return None

        def type_text(self, element, text):
            return None

        def get_tree(self, root, *, depth=None):
            return {}

        def screenshot(self, element=None):
            return None

    class BrokenBackend(GoodBackend):
        id = "plugin-broken"

        def __init__(self):
            raise RuntimeError("broken plugin")

    registry = {GoodBackend.id: GoodBackend, BrokenBackend.id: BrokenBackend}
    monkeypatch.setattr(backend, "_REGISTRY", registry)
    monkeypatch.setattr(backend, "_load_plugins", lambda: None)
    rows = backend.list_backends()
    by_id = {row["id"]: row for row in rows}
    assert by_id["plugin-good"]["available"] is True
    assert by_id["plugin-good"]["capabilities"] == ["click"]
    assert by_id["plugin-good"]["source"] == "plugin"
    assert by_id["plugin-broken"]["available"] is False
    assert "instantiation failed" in by_id["plugin-broken"]["description"]
    assert backend.supported_backends(Capability.CLICK) == ["plugin-good"]
    with pytest.raises(TypeError, match="Capability"):
        backend.supported_backends("click")  # type: ignore[arg-type]

    monkeypatch.setattr(sys, "platform", "darwin")
    assert isinstance(backend._auto_detect(), backend.MacOSAccessibilityBackend)
    monkeypatch.setattr(sys, "platform", "linux")
    assert isinstance(backend._auto_detect(), backend.LinuxATSPIBackend)
    monkeypatch.setattr(sys, "platform", "other")
    assert isinstance(backend._auto_detect(), backend.ImageBackend)


def test_backend_capability_contract_explains_unsupported_operation() -> None:
    from dolphin_desktop._backend import ImageBackend
    from dolphin_desktop._capabilities import Capability
    from dolphin_desktop._exceptions import UnsupportedCapabilityError

    assert ImageBackend().supports(Capability.CLICK)
    with pytest.raises(UnsupportedCapabilityError):
        ImageBackend().require_capability(Capability.INVOKE)


def test_backend_validation_and_safe_plugin_helpers_cover_bad_extensions() -> None:
    import dolphin_desktop._backend as backend
    from dolphin_desktop._capabilities import Capability

    with pytest.raises(TypeError, match=r"Capability\.INVOKE"):
        backend._check_capability_arg("INVOKE", "unit")
    with pytest.raises(TypeError, match="NoneType"):
        backend._check_capability_arg(None, "unit")
    fallback = backend._safe_call(lambda: (_ for _ in ()).throw(RuntimeError()), default="fallback")
    assert fallback == "fallback"

    good = _additional_backend_class(backend, "_unit_caps", capabilities=[Capability.CLICK, "bad"])
    assert backend._safe_capabilities(good()) == ["click"]
    none_caps = good()
    none_caps.capabilities = lambda: None
    assert backend._safe_capabilities(none_caps) == []
    failing = good()
    failing.capabilities = Mock(side_effect=RuntimeError("broken plugin"))
    assert backend._safe_capabilities(failing) == []


def test_backend_registry_loads_valid_plugins_and_skips_broken_entries(monkeypatch) -> None:
    import dolphin_desktop._backend as backend

    valid = _additional_backend_class(backend, "_unit_loaded")
    invalid_id = _additional_backend_class(backend, "")
    collision = _additional_backend_class(backend, "uia")

    class EntryPoint:
        def __init__(self, name, value=None, error=None):
            self.name = name
            self._value = value
            self._error = error

        def load(self):
            if self._error is not None:
                raise self._error
            return self._value

    entries = [
        EntryPoint("broken", error=ImportError("missing dependency")),
        EntryPoint("not-backend", object()),
        EntryPoint("invalid-id", invalid_id),
        EntryPoint("alias", valid),
        EntryPoint("replace-uia", collision),
    ]
    monkeypatch.setattr(importlib.metadata, "entry_points", lambda **kwargs: entries)
    monkeypatch.setattr(backend, "_REGISTRY", dict(backend._REGISTRY))

    with pytest.warns(RuntimeWarning) as warnings:
        backend._load_plugins_locked()

    assert backend._REGISTRY["_unit_loaded"] is valid
    assert backend._REGISTRY["uia"] is not collision
    messages = [str(w.message) for w in warnings]
    assert any("failed to load" in message for message in messages)
    assert any("does not point at a Backend" in message for message in messages)
    assert any("id" in message and "Skipping" in message for message in messages)
    assert any("does not match" in message for message in messages)
    assert any("tries to replace" in message for message in messages)


def test_backend_load_plugins_is_serialized_and_resolve_wraps_constructor_errors(
    monkeypatch,
) -> None:
    import dolphin_desktop._backend as backend

    loader = Mock()
    monkeypatch.setattr(backend, "_load_plugins_locked", loader)
    monkeypatch.setattr(backend, "_plugins_loaded", False)
    backend._load_plugins()
    backend._load_plugins()
    loader.assert_called_once_with()

    class Broken(_additional_backend_class(backend, "_unit_broken")):
        def __init__(self):
            raise RuntimeError("constructor failed")

    registry = dict(backend._REGISTRY)
    registry[Broken.id] = Broken
    monkeypatch.setattr(backend, "_REGISTRY", registry)
    with pytest.raises(ValueError, match="could not be instantiated"):
        backend.resolve(Broken.id)
