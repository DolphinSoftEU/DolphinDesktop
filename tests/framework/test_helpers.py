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


def test_helpers_expose_time_interpreter_and_binary_wrappers(monkeypatch) -> None:
    from dolphin_desktop import _helpers

    sleep = Mock()
    monkeypatch.setattr(_helpers._time, "sleep", sleep)
    monkeypatch.setattr(_helpers._time, "monotonic", Mock(return_value=12.5))
    monkeypatch.setattr(_helpers._sys, "executable", "python-under-test")

    _helpers.sleep(0.25)
    assert _helpers.monotonic() == 12.5
    assert _helpers.python_executable() == "python-under-test"
    sleep.assert_called_once_with(0.25)


def test_helpers_environment_platform_path_and_binary_lookup_wrappers(monkeypatch) -> None:
    import shutil

    from dolphin_desktop import _helpers

    monkeypatch.setenv("DOLPHIN_TEST_HELPER", "value")
    assert _helpers.env_var("DOLPHIN_TEST_HELPER") == "value"
    monkeypatch.delenv("DOLPHIN_TEST_HELPER")
    assert _helpers.env_var("DOLPHIN_TEST_HELPER") is None

    monkeypatch.setattr(_helpers._sys, "platform", "win32")
    assert _helpers.is_windows() is True
    monkeypatch.setattr(_helpers._sys, "platform", "linux")
    assert _helpers.is_windows() is False

    paths = ["existing"]
    monkeypatch.setattr(_helpers._sys, "path", paths)
    _helpers.add_import_path("existing")
    _helpers.add_import_path("new")
    assert paths == ["new", "existing"]

    monkeypatch.setattr(shutil, "which", Mock(return_value="C:/tools/demo.exe"))
    assert _helpers.which("demo") == "C:/tools/demo.exe"


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

    text_value = "Zażółć gęślą jaźń 🐬"
    text_path = Path(_helpers.temp_file(prefix="unit_", suffix=".txt", content=text_value))
    binary_path = Path(_helpers.temp_file(prefix="unit_", suffix=".bin", content=b"\x00\x01"))
    empty_path = Path(_helpers.temp_file(prefix="unit_", suffix=".empty"))
    assert text_path.read_bytes().decode("utf-8") == text_value
    assert binary_path.read_bytes() == b"\x00\x01"
    assert _helpers.path_exists(str(text_path))
    assert _helpers.path_basename(str(text_path)) == text_path.name
    _helpers.remove_file(str(text_path))
    _helpers.remove_file(str(text_path))
    with pytest.raises(FileNotFoundError):
        _helpers.remove_file(str(text_path), missing_ok=False)
    _helpers.remove_file(str(binary_path))
    _helpers.remove_file(str(empty_path))


def test_temp_file_closes_descriptor_when_opening_content_fails(
    monkeypatch, tmp_path: Path
) -> None:
    from dolphin_desktop import _helpers

    path = tmp_path / "failed.txt"
    close = Mock()
    monkeypatch.setattr(_helpers._tempfile, "mkstemp", Mock(return_value=(55, str(path))))
    monkeypatch.setattr(_helpers._os, "fdopen", Mock(side_effect=OSError("fdopen failed")))
    monkeypatch.setattr(_helpers._os, "close", close)

    with pytest.raises(OSError, match="fdopen failed"):
        _helpers.temp_file(content="secret")
    close.assert_called_once_with(55)
    assert not path.exists()

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

    connection.close.side_effect = OSError("already closed")
    monkeypatch.setattr(socket, "create_connection", Mock(return_value=connection))
    assert _helpers.tcp_reachable("localhost", 1234) is True

    class Response:
        status = 200

        def geturl(self):
            return "http://example.test"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    response = Response()
    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        Mock(return_value=Mock(open=Mock(return_value=response))),
    )
    assert _helpers.http_ok("http://example.test") is True
    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        Mock(return_value=Mock(open=Mock(side_effect=TimeoutError()))),
    )
    assert _helpers.http_ok("http://example.test") is False


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/canary.txt",
        "ftp://127.0.0.1/file",
        "http:///missing-host",
        "not a URL",
        "http://example.test:not-a-port/",
    ],
)
def test_http_ok_rejects_non_http_or_malformed_urls_before_open(monkeypatch, url: str) -> None:
    """Probes never open non-HTTP(S) resources."""
    import urllib.request

    from dolphin_desktop import _helpers

    opener = Mock()
    monkeypatch.setattr(urllib.request, "build_opener", Mock(return_value=opener))

    assert _helpers.http_ok(url) is False
    opener.open.assert_not_called()


def test_http_ok_rejects_redirect_to_non_http_resource(monkeypatch) -> None:
    import urllib.request

    from dolphin_desktop import _helpers

    class Response:
        status = 200

        def geturl(self):
            return "file:///C:/canary.txt"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        Mock(return_value=Mock(open=Mock(return_value=Response()))),
    )
    assert _helpers.http_ok("http://127.0.0.1:1234/redirect") is False


def test_http_ok_rejects_malformed_final_url(monkeypatch) -> None:
    import urllib.request

    from dolphin_desktop import _helpers

    class Response:
        status = 200

        def geturl(self):
            return "http://example.test:not-a-port/"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(
        urllib.request,
        "build_opener",
        Mock(return_value=Mock(open=Mock(return_value=Response()))),
    )
    assert _helpers.http_ok("http://example.test") is False


def test_http_ok_redirect_handler_rejects_non_http_targets(monkeypatch) -> None:
    import urllib.request

    from dolphin_desktop import _helpers

    opener = Mock(open=Mock(side_effect=TimeoutError()))
    build_opener = Mock(return_value=opener)
    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    assert _helpers.http_ok("http://example.test") is False

    (handler_type,) = build_opener.call_args.args
    handler = handler_type()
    assert handler.redirect_request(None, None, 302, "found", {}, "file:///secret") is None
    assert handler.redirect_request(None, None, 302, "found", {}, object()) is None
    request = urllib.request.Request("http://origin.example")
    redirected = handler.redirect_request(request, None, 302, "found", {}, "https://target.example")
    assert redirected is not None


def test_helpers_encode_and_escape_key_syntax() -> None:
    from dolphin_desktop._helpers import _escape_keys, b64decode, b64encode

    assert b64decode(b64encode("dolphin")) == b"dolphin"
    assert _escape_keys("a+b{c}") == "a{+}b{{}c{}}"
