"""Tests for the `dolphin spy` command's argument handling and pick wiring."""


from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dolphin_desktop import _cli, _spy


def _spy_args(**overrides) -> Namespace:
    args = {
        "sap": False,
        "jab": False,
        "cdp": None,
        "mainframe": None,
        "delphi": False,
        "backend": "uia",
        "image_pick": False,
        "pick": False,
        "window": None,
        "exact": None,
        "cls": None,
        "pid": None,
        "depth": None,
        "json": False,
    }
    args.update(overrides)
    return Namespace(**args)


@pytest.fixture
def captured_inspect(monkeypatch) -> dict:
    seen: dict = {}

    def _inspect(**kwargs):
        seen.update(kwargs)
        return {"schema_version": 1, "root": {"control_type": "Window", "children": []}}

    monkeypatch.setattr(_spy, "inspect", _inspect)
    return seen


class TestWindowArgument:
    def test_regex_metacharacters_are_escaped(self, captured_inspect):
        """--window is a substring to the user; 'a(b' must not reach the regex engine raw."""
        _cli._spy_cmd(_spy_args(window="a(b"))
        import re

        assert re.compile(captured_inspect["title_re"]).search("x a(b y")

    def test_plain_window_still_matches_as_a_substring(self, captured_inspect):
        _cli._spy_cmd(_spy_args(window="Notepad"))
        import re

        assert re.compile(captured_inspect["title_re"]).search("Untitled - Notepad")

    def test_exact_title_is_passed_through(self, captured_inspect):
        _cli._spy_cmd(_spy_args(exact="Notepad"))
        assert captured_inspect["title"] == "Notepad"


class TestPickWiring:
    def test_status_message_is_printed(self, monkeypatch, capsys):
        monkeypatch.setattr(
            _spy, "pick", lambda backend: _spy._pick_result("unreachable", [{"title": "OK"}])
        )
        _cli._spy_cmd(_spy_args(pick=True))
        assert "does NOT resolve" in capsys.readouterr().out

    def test_verified_pick_prints_no_warning(self, monkeypatch, capsys):
        monkeypatch.setattr(
            _spy, "pick", lambda backend: _spy._pick_result("ok", [{"title": "OK"}])
        )
        _cli._spy_cmd(_spy_args(pick=True))
        out = capsys.readouterr().out
        assert "warning" not in out
        assert "Locator call" in out

    def test_cancelled_pick_prints_no_locator(self, monkeypatch, capsys):
        monkeypatch.setattr(_spy, "pick", lambda backend: _spy._pick_result("cancelled", []))
        _cli._spy_cmd(_spy_args(pick=True))
        assert "Locator call" not in capsys.readouterr().out


class TestInitTimeouts:
    def test_pip_and_git_helpers_are_bounded(self):
        assert _cli._PIP_INSTALL_TIMEOUT > 0
        assert _cli._GIT_INIT_TIMEOUT > 0


def test_cli_spy_tree_mode_escapes_window_filter(monkeypatch, capsys) -> None:
    from dolphin_desktop import _cli as cli

    tree = {"root": {"control_type": "Window", "name": "Demo", "children": []}}
    with patch("dolphin_desktop._spy.inspect", return_value=tree) as inspect:
        cli._spy_cmd(
            SimpleNamespace(
                sap=False,
                jab=False,
                cdp=None,
                mainframe=None,
                delphi=False,
                image_pick=False,
                pick=False,
                output_dir=".",
                backend="uia",
                window="A+B",
                exact=None,
                cls=None,
                pid=None,
                depth=2,
                json=True,
            )
        )
    inspect.assert_called_once_with(backend="uia", title_re=r".*A\+B.*", depth=2)
    assert '"control_type": "Window"' in capsys.readouterr().out
