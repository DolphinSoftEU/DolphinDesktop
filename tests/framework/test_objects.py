"""Tests for the YAML-backed object repository."""


# Helpers

from __future__ import annotations

import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import dolphin_desktop as dolphin
from dolphin_desktop import AliasNotFoundError
from dolphin_desktop._exceptions import DolphinError
from dolphin_desktop._window import Window
from dolphin_desktop.objects import ObjectRepository, _map_selector


def _yaml_file(tmp_path: Path, content: str, name: str = "repo.yaml") -> Path:
    p = tmp_path / name
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def _window(spec: MagicMock | None = None) -> Window:
    return Window(spec if spec is not None else MagicMock())


# Selector key mapping


class TestSelectorKeyMap:
    def test_automation_id_mapped(self):
        assert _map_selector({"automation_id": "btnOK"}) == {"auto_id": "btnOK"}

    def test_role_mapped(self):
        assert _map_selector({"role": "Button"}) == {"control_type": "Button"}

    def test_name_mapped(self):
        assert _map_selector({"name": "OK"}) == {"title": "OK"}

    def test_unknown_keys_pass_through(self):
        assert _map_selector({"class_name": "TButton"}) == {"class_name": "TButton"}

    def test_mixed_mapping(self):
        result = _map_selector({"automation_id": "btn", "role": "Button", "title": "OK"})
        assert result == {"auto_id": "btn", "control_type": "Button", "title": "OK"}


# Schema validation


class TestValidation:
    def test_valid_minimal_entry(self, tmp_path):
        yaml_file = _yaml_file(
            tmp_path,
            """\
            submit_button:
              selector:
                automation_id: btnSubmit
        """,
        )
        repo = ObjectRepository()
        repo.load(yaml_file)
        entry = repo.resolve("submit_button")
        assert entry.selector == {"auto_id": "btnSubmit"}

    def test_top_level_not_mapping_raises(self, tmp_path):
        f = _yaml_file(tmp_path, "- item\n")
        repo = ObjectRepository()
        with pytest.raises(ValueError, match="top-level must be a mapping"):
            repo.load(f)

    def test_missing_selector_raises(self, tmp_path):
        f = _yaml_file(tmp_path, "btn:\n  fallback: []\n")
        repo = ObjectRepository()
        with pytest.raises(ValueError, match="missing required 'selector'"):
            repo.load(f)

    def test_selector_not_mapping_raises(self, tmp_path):
        f = _yaml_file(tmp_path, "btn:\n  selector: not_a_dict\n")
        repo = ObjectRepository()
        with pytest.raises(ValueError, match=r"'btn\.selector' must be a mapping"):
            repo.load(f)

    def test_fallback_not_list_raises(self, tmp_path):
        f = _yaml_file(tmp_path, "btn:\n  selector: {name: OK}\n  fallback: oops\n")
        repo = ObjectRepository()
        with pytest.raises(ValueError, match=r"'btn\.fallback' must be a list"):
            repo.load(f)

    def test_fallback_item_not_mapping_raises(self, tmp_path):
        f = _yaml_file(tmp_path, "btn:\n  selector: {name: OK}\n  fallback:\n    - oops\n")
        repo = ObjectRepository()
        with pytest.raises(ValueError, match=r"'btn\.fallback\[0\]'"):
            repo.load(f)

    def test_children_not_mapping_raises(self, tmp_path):
        f = _yaml_file(tmp_path, "win:\n  selector: {title: App}\n  children: bad\n")
        repo = ObjectRepository()
        with pytest.raises(ValueError, match=r"'win\.children' must be a mapping"):
            repo.load(f)

    def test_child_missing_selector_raises(self, tmp_path):
        f = _yaml_file(
            tmp_path,
            """\
            win:
              selector: {title: App}
              children:
                btn: {}
        """,
        )
        repo = ObjectRepository()
        with pytest.raises(ValueError, match=r"'win\.btn' missing required 'selector'"):
            repo.load(f)

    def test_empty_yaml_loads_ok(self, tmp_path):
        f = _yaml_file(tmp_path, "")
        repo = ObjectRepository()
        repo.load(f)
        assert repo.available() == []

    def test_nonexistent_file_raises(self):
        repo = ObjectRepository()
        with pytest.raises(FileNotFoundError):
            repo.load("nonexistent_file.yaml")

    def test_unknown_entry_fields_are_rejected(self, tmp_path):
        f = _yaml_file(tmp_path, "btn: {selector: {name: OK}, selektor: {name: typo}}\n")
        with pytest.raises(ValueError, match=r"unknown alias field.*selektor"):
            ObjectRepository().load(f)

    def test_duplicate_yaml_keys_include_nested_path_and_location(self, tmp_path):
        f = _yaml_file(
            tmp_path,
            """\
            win:
              selector: {title: App}
              children:
                btn:
                  selector:
                    name: OK
                    name: Cancel
        """,
        )
        with pytest.raises(ValueError, match=r"duplicate YAML key 'name'.*win\.btn\.selector"):
            ObjectRepository().load(f)

    def test_duplicate_sequence_yaml_keys_handle_unhashable_keys(self, tmp_path):
        f = _yaml_file(
            tmp_path,
            """\
            ? [one, two]
            : first
            ? [one, two]
            : second
        """,
        )
        with pytest.raises(ValueError, match=r"duplicate YAML key .* at '\$'"):
            ObjectRepository().load(f)

    def test_malformed_yaml_reports_location(self, tmp_path):
        f = _yaml_file(tmp_path, "btn: [\n")
        with pytest.raises(ValueError, match=r"malformed YAML at line 2, column"):
            ObjectRepository().load(f)

    def test_non_string_child_alias_is_rejected(self, tmp_path):
        f = _yaml_file(
            tmp_path,
            """\
            win:
              selector: {title: App}
              children:
                1:
                  selector: {name: Button}
        """,
        )
        with pytest.raises(ValueError, match=r"alias key must be a string, got 1"):
            ObjectRepository().load(f)

    def test_yaml_location_without_mark_is_empty(self):
        from dolphin_desktop.objects import _yaml_location

        assert _yaml_location(None) == ""

    def test_duplicate_error_exposes_its_context(self):
        from dolphin_desktop.objects import _DuplicateYamlKeyError

        mark = SimpleNamespace(line=1, column=2)
        error = _DuplicateYamlKeyError("name", "btn.selector", mark)
        assert error.key == "name"
        assert error.yaml_path == "btn.selector"
        assert error.mark is mark


# Loading and resolution


class TestLoading:
    def test_load_single_alias(self, tmp_path):
        f = _yaml_file(
            tmp_path,
            """\
            ok_button:
              selector:
                automation_id: btnOK
                role: Button
        """,
        )
        repo = ObjectRepository()
        repo.load(f)
        entry = repo.resolve("ok_button")
        assert entry.selector == {"auto_id": "btnOK", "control_type": "Button"}

    def test_load_fallback_preserved(self, tmp_path):
        f = _yaml_file(
            tmp_path,
            """\
            email_input:
              selector:
                automation_id: txtEmail
              fallback:
                - name: Email
                - name: "E-mail"
        """,
        )
        repo = ObjectRepository()
        repo.load(f)
        entry = repo.resolve("email_input")
        assert entry.fallback == [{"title": "Email"}, {"title": "E-mail"}]

    def test_load_children(self, tmp_path):
        f = _yaml_file(
            tmp_path,
            """\
            login_window:
              selector:
                title: Login - MyApp
              children:
                email_input:
                  selector:
                    automation_id: txtEmail
                submit_button:
                  selector:
                    automation_id: btnLogin
        """,
        )
        repo = ObjectRepository()
        repo.load(f)
        win_entry = repo.resolve("login_window")
        assert "email_input" in win_entry.children
        assert win_entry.children["email_input"].selector == {"auto_id": "txtEmail"}

    def test_load_multiple_files(self, tmp_path):
        f1 = _yaml_file(tmp_path, "btn_ok:\n  selector: {name: OK}\n", "a.yaml")
        f2 = _yaml_file(tmp_path, "btn_cancel:\n  selector: {name: Cancel}\n", "b.yaml")
        repo = ObjectRepository()
        repo.load(f1)
        repo.load(f2)
        assert repo.resolve("btn_ok").selector == {"title": "OK"}
        assert repo.resolve("btn_cancel").selector == {"title": "Cancel"}

    def test_available_returns_sorted_list(self, tmp_path):
        f = _yaml_file(
            tmp_path,
            """\
            zebra: {selector: {name: Z}}
            alpha: {selector: {name: A}}
            middle: {selector: {name: M}}
        """,
        )
        repo = ObjectRepository()
        repo.load(f)
        assert repo.available() == ["alpha", "middle", "zebra"]

    def test_clear_all(self, tmp_path):
        f = _yaml_file(tmp_path, "btn: {selector: {name: OK}}\n")
        repo = ObjectRepository()
        repo.load(f)
        repo.clear()
        assert repo.available() == []

    def test_clear_specific_level(self, tmp_path):
        f1 = _yaml_file(tmp_path, "a: {selector: {name: A}}\n", "a.yaml")
        f2 = _yaml_file(tmp_path, "b: {selector: {name: B}}\n", "b.yaml")
        repo = ObjectRepository()
        repo.load(f1, level="workspace")
        repo.load(f2, level="project")
        repo.clear(level="project")
        assert "a" in repo.available()
        assert "b" not in repo.available()

    def test_duplicate_alias_warns(self, tmp_path):
        f1 = _yaml_file(tmp_path, "btn: {selector: {name: OK}}\n", "a.yaml")
        f2 = _yaml_file(tmp_path, "btn: {selector: {name: Submit}}\n", "b.yaml")
        repo = ObjectRepository()
        repo.load(f1)
        with pytest.warns(UserWarning, match="btn"):
            repo.load(f2)
        # Later file wins
        assert repo.resolve("btn").selector == {"title": "Submit"}


# Override hierarchy


class TestOverrideHierarchy:
    def _repo_with_levels(self, tmp_path) -> ObjectRepository:
        repo = ObjectRepository()
        repo.load(
            _yaml_file(tmp_path, "btn: {selector: {name: Workspace}}\n", "w.yaml"),
            level="workspace",
        )
        repo.load(
            _yaml_file(tmp_path, "btn: {selector: {name: Project}}\n", "p.yaml"),
            level="project",
        )
        return repo

    def test_project_overrides_workspace(self, tmp_path):
        repo = self._repo_with_levels(tmp_path)
        assert repo.resolve("btn").selector == {"title": "Project"}

    def test_test_overrides_project(self, tmp_path):
        repo = self._repo_with_levels(tmp_path)
        repo.load(
            _yaml_file(tmp_path, "btn: {selector: {name: Test}}\n", "t.yaml"),
            level="test",
        )
        assert repo.resolve("btn").selector == {"title": "Test"}

    def test_workspace_level_reachable_when_not_overridden(self, tmp_path):
        repo = ObjectRepository()
        repo.load(
            _yaml_file(tmp_path, "only_here: {selector: {name: WS}}\n"),
            level="workspace",
        )
        assert repo.resolve("only_here").selector == {"title": "WS"}

    def test_invalid_level_raises(self, tmp_path):
        f = _yaml_file(tmp_path, "btn: {selector: {name: OK}}\n")
        repo = ObjectRepository()
        with pytest.raises(ValueError, match="level must be one of"):
            repo.load(f, level="invalid")


# AliasNotFoundError


class TestAliasNotFoundError:
    def test_raises_when_alias_missing(self, tmp_path):
        f = _yaml_file(tmp_path, "ok_btn: {selector: {name: OK}}\n")
        repo = ObjectRepository()
        repo.load(f)
        with pytest.raises(AliasNotFoundError, match="'missing_alias'"):
            repo.resolve("missing_alias")

    def test_error_lists_available_aliases(self, tmp_path):
        f = _yaml_file(tmp_path, "ok_btn: {selector: {name: OK}}\n")
        repo = ObjectRepository()
        repo.load(f)
        with pytest.raises(AliasNotFoundError) as exc_info:
            repo.resolve("nope")
        assert "ok_btn" in str(exc_info.value)

    def test_alias_not_found_is_dolphin_error(self):
        assert issubclass(AliasNotFoundError, DolphinError)

    def test_child_not_found_lists_children(self, tmp_path):
        f = _yaml_file(
            tmp_path,
            """\
            win:
              selector: {title: App}
              children:
                btn: {selector: {name: OK}}
        """,
        )
        repo = ObjectRepository()
        repo.load(f)
        with pytest.raises(AliasNotFoundError) as exc_info:
            repo.resolve_child("win", "unknown_child")
        msg = str(exc_info.value)
        assert "unknown_child" in msg
        assert "btn" in msg


# Discover (auto-discovery)


class TestDiscover:
    def test_discover_loads_yaml_files(self, tmp_path):
        objects_dir = tmp_path / "objects"
        objects_dir.mkdir()
        (objects_dir / "a.yaml").write_text("btn_a: {selector: {name: A}}\n")
        (objects_dir / "b.yaml").write_text("btn_b: {selector: {name: B}}\n")
        repo = ObjectRepository()
        count = repo.discover(objects_dir)
        assert count == 2
        assert "btn_a" in repo.available()
        assert "btn_b" in repo.available()

    def test_discover_returns_zero_if_dir_missing(self, tmp_path):
        repo = ObjectRepository()
        count = repo.discover(tmp_path / "nonexistent")
        assert count == 0

    def test_discover_loads_yml_extension(self, tmp_path):
        d = tmp_path / "objects"
        d.mkdir()
        (d / "items.yml").write_text("item: {selector: {name: Item}}\n")
        repo = ObjectRepository()
        count = repo.discover(d)
        assert count == 1
        assert repo.resolve("item").selector == {"title": "Item"}


# Window.element() integration


class TestWindowElement:
    def test_element_returns_locator(self, tmp_path):
        from dolphin_desktop._locator import Locator

        f = _yaml_file(tmp_path, "ok_btn: {selector: {automation_id: btnOK}}\n")
        import dolphin_desktop.objects as obj_module

        obj_module._repository.clear()
        obj_module._repository.load(f)
        try:
            win = _window()
            loc = win.element("ok_btn")
            assert isinstance(loc, Locator)
            assert loc._criteria.get("auto_id") == "btnOK"
        finally:
            obj_module._repository.clear()

    def test_element_resolves_child_when_window_has_alias(self, tmp_path):
        from dolphin_desktop._locator import Locator

        f = _yaml_file(
            tmp_path,
            """\
            login_window:
              selector: {title: Login}
              children:
                email_input:
                  selector: {automation_id: txtEmail}
        """,
        )
        import dolphin_desktop.objects as obj_module

        obj_module._repository.clear()
        obj_module._repository.load(f)
        try:
            win = _window()
            win._alias = "login_window"  # type: ignore[attr-defined]
            loc = win.element("email_input")
            assert isinstance(loc, Locator)
            assert loc._criteria.get("auto_id") == "txtEmail"
        finally:
            obj_module._repository.clear()

    def test_element_raises_when_alias_not_found(self):
        import dolphin_desktop.objects as obj_module

        obj_module._repository.clear()
        try:
            win = _window()
            with pytest.raises(AliasNotFoundError):
                win.element("nonexistent")
        finally:
            obj_module._repository.clear()

    def test_element_with_fallback_propagated(self, tmp_path):
        f = _yaml_file(
            tmp_path,
            """\
            email_input:
              selector: {automation_id: txtEmail}
              fallback:
                - name: Email
        """,
        )
        import dolphin_desktop.objects as obj_module

        obj_module._repository.clear()
        obj_module._repository.load(f)
        try:
            win = _window()
            loc = win.element("email_input")
            assert loc._fallback == [{"title": "Email"}]
        finally:
            obj_module._repository.clear()


# Public module API (dolphin.objects)


class TestPublicModuleAPI:
    def setup_method(self):
        dolphin.objects.clear()

    def teardown_method(self):
        dolphin.objects.clear()

    def test_load_and_available(self, tmp_path):
        f = _yaml_file(tmp_path, "btn: {selector: {name: OK}}\n")
        dolphin.objects.load(str(f))
        assert "btn" in dolphin.objects.available()

    def test_clear_empties_registry(self, tmp_path):
        f = _yaml_file(tmp_path, "btn: {selector: {name: OK}}\n")
        dolphin.objects.load(str(f))
        dolphin.objects.clear()
        assert dolphin.objects.available() == []

    def test_alias_not_found_error_accessible_from_top_level(self):
        assert hasattr(dolphin, "AliasNotFoundError")
        assert issubclass(dolphin.AliasNotFoundError, dolphin.DolphinError)

    def test_objects_module_accessible_from_top_level(self):
        assert hasattr(dolphin, "objects")
        assert hasattr(dolphin.objects, "load")
        assert hasattr(dolphin.objects, "discover")
        assert hasattr(dolphin.objects, "clear")
        assert hasattr(dolphin.objects, "available")


def test_object_repository_resolves_the_highest_priority_level() -> None:
    from dolphin_desktop.objects import ObjectEntry, ObjectRepository

    repository = ObjectRepository()
    repository._registry["workspace"]["save"] = ObjectEntry({"title": "Workspace"})
    repository._registry["test"]["save"] = ObjectEntry({"title": "Test"})
    assert repository.resolve("save").selector == {"title": "Test"}
    with pytest.raises(Exception, match="missing"):
        repository.resolve("missing")


def test_objects_parse_repository_entry_aliases() -> None:
    from dolphin_desktop.objects import _parse_entry

    entry = _parse_entry({"selector": {"name": "Save"}, "fallback": [{"role": "Button"}]})
    assert entry.selector == {"title": "Save"}
    assert entry.fallback == [{"control_type": "Button"}]
