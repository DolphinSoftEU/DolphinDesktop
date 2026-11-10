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
    @pytest.mark.parametrize("stage", ["selector", "parent", "validation", "owner", "image_name"])
    def test_main_reports_denial_after_selection_or_during_owner_check(
        self, monkeypatch, capsys, tmp_path, stage
    ):
        mode = "--image-pick" if stage == "image_name" else "--pick"
        output_dir = tmp_path / "templates"
        monkeypatch.setattr("sys.argv", ["dolphin", "spy", mode, "--output-dir", str(output_dir)])
        state = {"selected": False, "polls": 0}
        denial = Exception(-2147024891, "localized provider denial")
        owner = SimpleNamespace(control_type="Window", parent=None, name="AUT", class_name="AUT")

        class Info:
            rectangle = SimpleNamespace(left=0, top=0, right=20, bottom=20)
            control_type = "Button"
            automation_id = "save"
            class_name = "Button"

            @property
            def name(self):
                if state["selected"] and stage in ("selector", "image_name"):
                    raise denial
                return "Save"

            @property
            def parent(self):
                if (state["selected"] and stage == "parent") or (
                    state["polls"] > 1 and stage == "owner"
                ):
                    raise denial
                return owner

        def cursor():
            state["polls"] += 1
            assert state["polls"] <= 2, "picker must abort instead of retrying denial"
            return (1, 2)

        def key_state(key):
            if key in (1, 17) and stage != "owner":
                if key == 17:
                    state["selected"] = True
                return 0x8000
            return 0

        def denied_desktop(**kwargs):
            assert state["selected"], "verification must happen after Ctrl+Click"
            raise denial

        with (
            patch.dict(
                "sys.modules",
                {
                    "win32api": SimpleNamespace(GetCursorPos=cursor, GetAsyncKeyState=key_state),
                    "win32con": SimpleNamespace(VK_ESCAPE=27, VK_LBUTTON=1, VK_CONTROL=17),
                    "pywinauto": SimpleNamespace(Desktop=denied_desktop),
                },
            ),
            patch("dolphin_desktop._spy._element_info_from_point", return_value=Info()),
            patch("dolphin_desktop._spy._Highlighter.update") as highlight,
            patch("dolphin_desktop._spy._Highlighter.clear") as cleanup,
            patch("PIL.ImageGrab.grab") as grab,
            patch("dolphin_desktop._spy._PICK_POLL", 0),
            pytest.raises(SystemExit) as caught,
        ):
            _cli.main()
        assert caught.value.code == 1
        assert state["selected"] == (stage != "owner")
        assert highlight.call_count >= 1
        cleanup.assert_called_once_with()
        grab.assert_not_called()
        grab.return_value.save.assert_not_called()
        output = capsys.readouterr()
        assert "error:" in output.err and "E_ACCESSDENIED" in output.err
        assert "Traceback" not in output.err
        assert "Cancelled." not in output.out
        assert "Locator call" not in output.out
        assert "Template saved:" not in output.out
        assert "unverified" not in output.out and "unreachable" not in output.out
        assert not output_dir.exists()
        assert list(tmp_path.iterdir()) == []

    @pytest.mark.parametrize("mode", ["--pick", "--image-pick"])
    def test_main_reports_provider_denial_and_cleans_up(self, monkeypatch, capsys, tmp_path, mode):
        monkeypatch.setattr("sys.argv", ["dolphin", "spy", mode, "--output-dir", str(tmp_path)])
        clear = patch("dolphin_desktop._spy._Highlighter.clear")
        error = Exception(-2147024891, "localized provider diagnostic")
        with (
            patch.dict(
                "sys.modules",
                {
                    "win32api": SimpleNamespace(
                        GetCursorPos=lambda: (1, 2), GetAsyncKeyState=lambda key: 0
                    ),
                    "win32con": SimpleNamespace(VK_ESCAPE=27),
                },
            ),
            patch("dolphin_desktop._spy._element_info_from_point", side_effect=error) as lookup,
            clear as cleanup,
            pytest.raises(SystemExit) as caught,
        ):
            _cli.main()
        assert caught.value.code == 1
        lookup.assert_called_once_with(1, 2)
        cleanup.assert_called_once_with()
        output = capsys.readouterr()
        assert "error:" in output.err and "E_ACCESSDENIED" in output.err
        assert "Traceback" not in output.err
        assert "Cancelled." not in output.out
        assert "Locator call" not in output.out
        assert list(tmp_path.iterdir()) == []

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
