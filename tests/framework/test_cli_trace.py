"""Tests for ``dolphin trace view`` on a directory that holds no usable trace."""

from __future__ import annotations

import argparse
import webbrowser

import pytest

from dolphin_desktop import _cli


@pytest.fixture
def no_browser(monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    return opened


def _args(trace_dir, run_dir) -> argparse.Namespace:
    return argparse.Namespace(dir=str(trace_dir), last=False, run_id=str(run_dir))


class TestTraceViewWithoutADatabase:
    def _run_dir(self, tmp_path):
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        return run_dir

    def test_exits_with_a_message_not_a_traceback(self, tmp_path, capsys, no_browser):
        """Any directory passes the is_dir() check, including one holding no trace."""
        run_dir = self._run_dir(tmp_path)
        with pytest.raises(SystemExit) as exc:
            _cli._trace_view_cmd(_args(tmp_path, run_dir))
        assert exc.value.code == 1
        assert "Error" in capsys.readouterr().out
        assert no_browser == []

    def test_no_database_is_left_behind(self, tmp_path, capsys, no_browser):
        run_dir = self._run_dir(tmp_path)
        with pytest.raises(SystemExit):
            _cli._trace_view_cmd(_args(tmp_path, run_dir))
        assert list(run_dir.iterdir()) == []

    def test_a_file_that_is_not_a_database_is_reported(self, tmp_path, capsys, no_browser):
        run_dir = self._run_dir(tmp_path)
        (run_dir / "trace.db").write_text("not a database", encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            _cli._trace_view_cmd(_args(tmp_path, run_dir))
        assert exc.value.code == 1
        assert "Error" in capsys.readouterr().out
