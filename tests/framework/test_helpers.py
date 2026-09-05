"""Tests for :mod:`dolphin_desktop._helpers`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest


def test_helpers_counter_thread_and_path_utilities() -> None:
    from dolphin_desktop._helpers import counter, dirname, path_join, start_thread

    numbers = counter(4)
    assert [next(numbers), next(numbers), next(numbers)] == [4, 5, 6]
    received: list[str] = []
    thread = start_thread(received.append, args=("done",))
    thread.join(timeout=1)
    assert received == ["done"]
    assert dirname(path_join("one", "two", "file.txt")).endswith("one\\two")


def test_helpers_parse_processes_and_find_pid_without_tasklist(monkeypatch) -> None:
    from dolphin_desktop import _helpers

    running_processes = _helpers.running_processes
    monkeypatch.setattr(
        _helpers._subprocess,
        "check_output",
        lambda *args, **kwargs: '"Alpha.EXE","12","Console"\nmalformed\n"bad","x"\n',
    )
    assert _helpers.running_processes() == [("Alpha.EXE", 12)]
    monkeypatch.setattr(_helpers, "running_processes", lambda: [("SAPLOGON.EXE", 55)])
    assert _helpers.find_pid_by_image_name("saplogon.exe") == 55
    assert _helpers.find_pid_by_image_name("missing.exe") is None

    monkeypatch.setattr(_helpers, "running_processes", running_processes)
    monkeypatch.setattr(
        _helpers._subprocess,
        "check_output",
        Mock(side_effect=OSError("no tasklist")),
    )
    assert _helpers.running_processes() == []


def test_helpers_file_context_and_path_wrappers(tmp_path: Path) -> None:
    from dolphin_desktop import _helpers

    text_path = Path(_helpers.temp_file(prefix="unit_", suffix=".txt", content="hello"))
    binary_path = Path(_helpers.temp_file(prefix="unit_", suffix=".bin", content=b"\x00\x01"))
    assert text_path.read_text() == "hello"
    assert binary_path.read_bytes() == b"\x00\x01"
    assert _helpers.path_exists(str(text_path))
    assert _helpers.path_basename(str(text_path)) == text_path.name
    _helpers.remove_file(str(text_path))
    _helpers.remove_file(str(text_path))
    with pytest.raises(FileNotFoundError):
        _helpers.remove_file(str(text_path), missing_ok=False)
    _helpers.remove_file(str(binary_path))

    with _helpers.tempdir(prefix="unit_") as directory:
        assert Path(directory).is_dir()
        created = Path(directory) / "file.txt"
        created.write_text("ok")
    assert not Path(directory).exists()


def test_helpers_network_probes_return_boolean_results(monkeypatch) -> None:
    import socket
    import urllib.request

    from dolphin_desktop import _helpers

    connection = Mock()
    monkeypatch.setattr(socket, "create_connection", Mock(return_value=connection))
    assert _helpers.tcp_reachable("localhost", 1234, timeout=0.2) is True
    connection.close.assert_called_once_with()
    monkeypatch.setattr(socket, "create_connection", Mock(side_effect=OSError("offline")))
    assert _helpers.tcp_reachable("localhost", 1234) is False

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    response = Response()
    monkeypatch.setattr(urllib.request, "urlopen", Mock(return_value=response))
    assert _helpers.http_ok("http://example.test") is True
    monkeypatch.setattr(urllib.request, "urlopen", Mock(side_effect=TimeoutError()))
    assert _helpers.http_ok("http://example.test") is False


def test_helpers_encode_and_escape_key_syntax() -> None:
    from dolphin_desktop._helpers import _escape_keys, b64decode, b64encode

    assert b64decode(b64encode("dolphin")) == b"dolphin"
    assert _escape_keys("a+b{c}") == "a{+}b{{}c{}}"
