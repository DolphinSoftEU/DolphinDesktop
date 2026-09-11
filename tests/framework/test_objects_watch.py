"""Tests for the object repository's error surfaces and reload-on-change semantics."""

from __future__ import annotations

import inspect
import itertools
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from dolphin_desktop import AliasNotFoundError, objects
from dolphin_desktop._window import Window
from dolphin_desktop.objects import ObjectRepository


def _yaml_file(tmp_path: Path, content: str, name: str = "repo.yaml") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


_mtime_step = itertools.count(10)


def _touch_future(path: Path) -> None:
    """Bump mtime strictly past every earlier bump.

    The offset grows on every call instead of being a fixed ``+10``:
    ``time.time()`` advances in ~15.6 ms ticks on Windows, so two bumps
    inside one tick produced the *same* mtime, the repository correctly
    saw no change, and a test expecting a reload failed depending on
    where the run happened to fall in the tick.
    """
    future = time.time() + next(_mtime_step)
    os.utime(path, (future, future))


# clear() must reject a bad level the same way load() does


class TestClearLevelValidation:
    def test_unknown_level_raises_value_error(self):
        repo = ObjectRepository()
        with pytest.raises(ValueError, match="level must be one of"):
            repo.clear(level="bogus")

    def test_error_matches_loads_error_type(self, tmp_path):
        repo = ObjectRepository()
        with pytest.raises(ValueError):
            repo.load(_yaml_file(tmp_path, "b: {selector: {name: OK}}\n"), level="bogus")

    def test_known_level_still_clears(self, tmp_path):
        repo = ObjectRepository()
        repo.load(_yaml_file(tmp_path, "b: {selector: {name: OK}}\n"))
        repo.clear(level="project")
        assert repo.available() == []


# Unknown selector keys are typos, and must be reported at the file that holds them


class TestSelectorKeyValidation:
    def test_typo_is_rejected_at_load(self, tmp_path):
        f = _yaml_file(tmp_path, "btn: {selector: {automationid: btnOK}}\n")
        repo = ObjectRepository()
        with pytest.raises(ValueError, match="automationid"):
            repo.load(f)

    def test_error_names_the_offending_alias(self, tmp_path):
        f = _yaml_file(tmp_path, "btn: {selector: {nope: x}}\n")
        repo = ObjectRepository()
        with pytest.raises(ValueError, match=r"btn\.selector"):
            repo.load(f)

    def test_fallback_keys_are_validated_too(self, tmp_path):
        f = _yaml_file(
            tmp_path, "btn:\n  selector: {name: OK}\n  fallback:\n    - automationid: x\n"
        )
        repo = ObjectRepository()
        with pytest.raises(ValueError, match=r"fallback\[0\]"):
            repo.load(f)

    def test_child_selector_keys_are_validated_too(self, tmp_path):
        f = _yaml_file(
            tmp_path,
            "win:\n  selector: {title: App}\n  children:\n    btn:\n      selector: {nope: x}\n",
        )
        repo = ObjectRepository()
        with pytest.raises(ValueError, match=r"win\.btn\.selector"):
            repo.load(f)

    @pytest.mark.parametrize(
        "key", ["automation_id", "role", "name", "title", "title_re", "class_name", "found_index"]
    )
    def test_supported_keys_are_accepted(self, tmp_path, key):
        repo = ObjectRepository()
        repo.load(_yaml_file(tmp_path, f"btn: {{selector: {{{key}: x}}}}\n"))
        assert repo.available() == ["btn"]

    def test_parent_is_rejected(self, tmp_path):
        """Window.element() passes the window as Locator's positional 'parent'.

        A selector carrying the key would reach Locator as a duplicate argument and
        fail with a TypeError far from the YAML that caused it.
        """
        repo = ObjectRepository()
        with pytest.raises(ValueError, match="parent"):
            repo.load(_yaml_file(tmp_path, "btn: {selector: {parent: win, name: OK}}\n"))

    def test_parent_would_collide_with_locators_own_argument(self):
        from dolphin_desktop._locator import Locator

        assert list(inspect.signature(Locator.__init__).parameters)[1] == "parent"


# Reload-on-change must reflect deletions and must not hide broken files


class TestWatchReload:
    def _watching(self, tmp_path, content: str) -> tuple[ObjectRepository, Path]:
        path = _yaml_file(tmp_path, content)
        repo = ObjectRepository()
        repo.load(path)
        repo.enable_watch()
        return repo, path

    def test_edited_alias_is_picked_up(self, tmp_path):
        repo, path = self._watching(tmp_path, "btn: {selector: {name: Old}}\n")
        path.write_text("btn: {selector: {name: New}}\n", encoding="utf-8")
        _touch_future(path)
        assert repo.resolve("btn").selector == {"title": "New"}

    def test_existing_window_element_locator_refreshes_its_alias(self, tmp_path, monkeypatch):
        """A lazy alias locator must use the watched selector on its next resolve."""
        repo, path = self._watching(tmp_path, "btn: {selector: {name: Old}}\n")
        monkeypatch.setattr(objects, "_repository", repo)
        window = Window(Mock())
        window._alias = None
        locator = window.element("btn")
        assert locator._criteria == {"title": "Old"}

        calls: list[dict[str, str]] = []
        parent = SimpleNamespace(
            child_window=lambda **criteria: calls.append(criteria) or SimpleNamespace()
        )
        monkeypatch.setattr(
            locator,
            "_get_parent_spec",
            lambda deadline=None, single_attempt=False: parent,
        )
        monkeypatch.setattr(
            "dolphin_desktop._locator._wait_until_visible",
            lambda spec, timeout: None,
        )

        locator._resolve(single_attempt=True)
        path.write_text("btn: {selector: {name: New}}\n", encoding="utf-8")
        _touch_future(path)
        locator._resolve(single_attempt=True)

        assert calls == [{"title": "Old"}, {"title": "New"}]

    def test_existing_window_element_locator_refreshes_its_fallback(self, tmp_path, monkeypatch):
        """A lazy alias locator must use the watched fallback on its next resolve."""
        repo, path = self._watching(
            tmp_path,
            """\
            btn:
              selector: {name: Missing primary}
              fallback:
                - name: Old
            """,
        )
        monkeypatch.setattr(objects, "_repository", repo)
        window = Window(Mock())
        window._alias = None
        locator = window.element("btn")
        assert locator._criteria == {"title": "Missing primary"}
        assert locator._fallback == [{"title": "Old"}]

        old = Mock()
        old.window_text.return_value = "Old"
        new = Mock()
        new.window_text.return_value = "New"
        calls: list[dict[str, str]] = []

        def child_window(**criteria):
            calls.append(criteria)
            if criteria == {"title": "Missing primary"}:
                raise LookupError("primary selector is intentionally missing")
            return {"Old": old, "New": new}[criteria["title"]]

        parent = SimpleNamespace(child_window=child_window)
        monkeypatch.setattr(
            locator,
            "_get_parent_spec",
            lambda deadline=None, single_attempt=False: parent,
        )
        monkeypatch.setattr(
            "dolphin_desktop._locator._wait_until_visible",
            lambda spec, timeout: None,
        )
        monkeypatch.setattr("dolphin_desktop._selfheal.record_fallback", lambda *args: None)

        assert locator.text() == "Old"
        path.write_text(
            """\
            btn:
              selector: {name: Missing primary}
              fallback:
                - name: New
            """,
            encoding="utf-8",
        )
        _touch_future(path)

        assert locator.text() == "New"
        assert locator._fallback == [{"title": "New"}]
        locator.click()

        assert old.click_input.call_count == 0
        assert new.click_input.call_count == 1
        assert calls == [
            {"title": "Missing primary"},
            {"title": "Old"},
            {"title": "Missing primary"},
            {"title": "New"},
            {"title": "Missing primary"},
            {"title": "New"},
        ]

    def test_deleted_alias_stops_resolving(self, tmp_path):
        """update() alone can never drop an alias the user removed from the YAML."""
        repo, path = self._watching(
            tmp_path, "keep: {selector: {name: K}}\ngone: {selector: {name: G}}\n"
        )
        path.write_text("keep: {selector: {name: K}}\n", encoding="utf-8")
        _touch_future(path)
        with pytest.raises(AliasNotFoundError):
            repo.resolve("gone")

    def test_surviving_alias_is_kept(self, tmp_path):
        repo, path = self._watching(
            tmp_path, "keep: {selector: {name: K}}\ngone: {selector: {name: G}}\n"
        )
        path.write_text("keep: {selector: {name: K}}\n", encoding="utf-8")
        _touch_future(path)
        assert repo.resolve("keep").selector == {"title": "K"}

    def test_aliases_from_a_sibling_file_at_the_same_level_survive(self, tmp_path):
        a = _yaml_file(tmp_path, "from_a: {selector: {name: A}}\n", "a.yaml")
        b = _yaml_file(tmp_path, "from_b: {selector: {name: B}}\n", "b.yaml")
        repo = ObjectRepository()
        repo.load(a)
        repo.load(b)
        repo.enable_watch()
        b.write_text("from_b: {selector: {name: B2}}\n", encoding="utf-8")
        _touch_future(b)
        assert repo.resolve("from_a").selector == {"title": "A"}
        assert repo.resolve("from_b").selector == {"title": "B2"}

    def test_broken_file_is_reported_and_leaves_the_old_definitions(self, tmp_path, caplog):
        repo, path = self._watching(tmp_path, "btn: {selector: {name: Old}}\n")
        path.write_text("btn: {selector: {automationid: oops}}\n", encoding="utf-8")
        _touch_future(path)
        with caplog.at_level("WARNING", logger="dolphin_desktop.objects"):
            assert repo.resolve("btn").selector == {"title": "Old"}
        assert "reload of level" in caplog.text

    @pytest.mark.parametrize(
        ("content", "type_name"),
        [("[]\n", "list"), ("false\n", "bool"), ("0\n", "int"), ("''\n", "str")],
    )
    def test_falsey_top_level_value_is_rejected_without_dropping_alias(
        self, tmp_path, caplog, content, type_name
    ):
        repo, path = self._watching(tmp_path, "btn: {selector: {name: Old}}\n")
        path.write_text(content, encoding="utf-8")
        _touch_future(path)
        with caplog.at_level("WARNING", logger="dolphin_desktop.objects"):
            assert repo.resolve("btn").selector == {"title": "Old"}
        assert f"top-level must be a mapping, got {type_name}" in caplog.text

    def _two_files(self, tmp_path) -> tuple[ObjectRepository, Path, Path]:
        a = _yaml_file(tmp_path, "from_a: {selector: {name: A}}\n", "a.yaml")
        b = _yaml_file(tmp_path, "from_b: {selector: {name: B}}\n", "b.yaml")
        repo = ObjectRepository()
        repo.load(a)
        repo.load(b)
        repo.enable_watch()
        return repo, a, b

    def test_a_deleted_file_does_not_wedge_its_level(self, tmp_path):
        """Rebuilding a level must not fail because one of its files is gone."""
        repo, a, b = self._two_files(tmp_path)
        b.unlink()
        a.write_text("from_a: {selector: {name: A2}}\n", encoding="utf-8")
        _touch_future(a)
        assert repo.resolve("from_a").selector == {"title": "A2"}

    def test_a_deleted_files_aliases_stop_resolving(self, tmp_path):
        repo, a, b = self._two_files(tmp_path)
        b.unlink()
        a.write_text("from_a: {selector: {name: A2}}\n", encoding="utf-8")
        _touch_future(a)
        with pytest.raises(AliasNotFoundError):
            repo.resolve("from_b")

    def test_the_level_still_reloads_after_a_deletion(self, tmp_path):
        repo, a, b = self._two_files(tmp_path)
        b.unlink()
        a.write_text("from_a: {selector: {name: A2}}\n", encoding="utf-8")
        _touch_future(a)
        repo.resolve("from_a")
        a.write_text("from_a: {selector: {name: A3}}\n", encoding="utf-8")
        _touch_future(a)
        assert repo.resolve("from_a").selector == {"title": "A3"}

    def test_a_deleted_file_is_reported_once_not_per_lookup(self, tmp_path, caplog):
        repo, _a, b = self._two_files(tmp_path)
        b.unlink()
        with caplog.at_level("WARNING", logger="dolphin_desktop.objects"):
            for _ in range(5):
                repo.resolve("from_a")
        assert len(caplog.records) == 1

    def _block_open(self, monkeypatch, blocked: set[Path]) -> None:
        """Make ``open()`` fail on *blocked* while ``stat()`` keeps working.

        That is a Windows sharing violation on the file that was just saved — the
        editor still holds it — not a deletion.
        """
        real_open = Path.open

        def _open(self, *args, **kwargs):
            if self in blocked:
                raise PermissionError(32, "used by another process")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", _open)

    def test_an_unopenable_file_comes_back_when_it_can_be_read_again(self, tmp_path, monkeypatch):
        """Dropping the file would make its aliases unrecoverable for the whole run."""
        repo, a, b = self._two_files(tmp_path)
        blocked = {b}
        self._block_open(monkeypatch, blocked)
        a.write_text("from_a: {selector: {name: A2}}\n", encoding="utf-8")
        _touch_future(a)
        with pytest.raises(AliasNotFoundError):
            repo.resolve("from_b")

        blocked.clear()
        _touch_future(a)
        assert repo.resolve("from_b").selector == {"title": "B"}

    def test_an_unopenable_file_is_reported_once_not_per_rebuild(
        self, tmp_path, monkeypatch, caplog
    ):
        repo, a, b = self._two_files(tmp_path)
        self._block_open(monkeypatch, {b})
        with caplog.at_level("WARNING", logger="dolphin_desktop.objects"):
            for i in range(3):
                a.write_text(f"from_a: {{selector: {{name: A{i}}}}}\n", encoding="utf-8")
                _touch_future(a)
                repo.resolve("from_a")
        assert len(caplog.records) == 1

    def test_a_fixed_file_is_picked_up_after_a_failure(self, tmp_path):
        repo, path = self._watching(tmp_path, "btn: {selector: {name: Old}}\n")
        path.write_text("btn: {selector: {automationid: oops}}\n", encoding="utf-8")
        _touch_future(path)
        repo.resolve("btn")  # failed reload must not consume the mtime bump
        path.write_text("btn: {selector: {name: Fixed}}\n", encoding="utf-8")
        _touch_future(path)
        assert repo.resolve("btn").selector == {"title": "Fixed"}
