"""Display-only redaction must retain identities, other tests and writer lifetime."""

from __future__ import annotations

import pytest

from dolphin_desktop import path_join
from dolphin_desktop._terminal import _display_identifiers, _TerminalRedactor
from tests._allure_redaction import terminal_session
from tests._terminal_redaction import (
    IS_WINDOWS,
    invocation_paths,
    run_terminal_case,
    windows_drive_paths,
)


@pytest.mark.parametrize("workers", [False, True], ids=["serial", "xdist"])
@pytest.mark.parametrize("mode", ["success", "call", "teardown"])
def test_real_plugin_collects_runs_and_redacts_from_subdirectory(tmp_path, workers, mode):
    code, output = run_terminal_case(tmp_path, workers=workers, mode=mode)
    assert code == (0 if mode == "success" else 1), output
    assert "INTERNALERROR" not in output
    assert "KAN763_CANARY_739184" not in output, output
    assert "test_case.py::test_auth[case-***]" in output, output
    assert "test_case.py::test_public" in output, output
    assert ("2 passed" if mode != "call" else "1 passed") in output, output


def test_real_plugin_redacts_collect_only_identifiers(tmp_path):
    code, output = run_terminal_case(tmp_path, workers=False, mode="collection")
    assert code == 0, output
    assert "KAN763_CANARY_739184" not in output, output
    assert "test_auth[case-***]" in output, output
    assert "2 tests collected" in output, output


def test_real_plugin_runs_minimal_test_without_a_backend_provider(tmp_path):
    code, output = run_terminal_case(tmp_path, workers=False, mode="smoke")
    assert code == 0, output
    assert "1 passed" in output, output


def test_terminal_masks_identifiers_without_masking_unrelated_words_or_changing_items():
    session, writer, writes, cleanups = terminal_session()
    original = writer.write
    redactor = _TerminalRedactor(lambda item: {"login"})
    redactor.pytest_configure(session.config)
    redactor.pytest_sessionstart(session)

    class Item:
        nodeid = "test_case.py::test_auth[login]"

    item = Item()
    redactor.pytest_collection_modifyitems(session.config, [item])
    start = redactor.pytest_runtest_logstart(item.nodeid)
    next(start)
    writer.write(item.nodeid, green=True)
    with pytest.raises(StopIteration):
        next(start)
    writer.write("test_login public login message")
    assert writes == [
        ("test_case.py::test_auth[***]", {"green": True}),
        ("test_login public login message", {}),
    ]
    assert item.nodeid == "test_case.py::test_auth[login]"
    cleanups[0]()
    assert writer.write is original


def test_terminal_defers_xdist_start_until_report_and_restores_state_on_exception():
    session, writer, writes, cleanups = terminal_session(workers=True)
    redactor = _TerminalRedactor(lambda item: set())
    redactor.pytest_configure(session.config)
    redactor.pytest_sessionstart(session)
    start = redactor.pytest_runtest_logstart("test_case.py::test_auth[739184]")
    next(start)
    writer.write("test_case.py::test_auth[739184]")
    writer.write("worker progress")
    with pytest.raises(RuntimeError, match="interrupted"):
        start.throw(RuntimeError("interrupted"))
    report = pytest.TestReport(
        nodeid="test_case.py::test_auth[739184]",
        location=("test_case.py", 1, "test_auth"),
        keywords={},
        outcome="passed",
        longrepr=None,
        when="call",
    )
    report._dolphin_parameter_values = ("739184",)
    redactor.pytest_runtest_logreport(report)
    writer.write("PASSED " + report.nodeid)
    assert writes == [("worker progress", {}), ("PASSED test_case.py::test_auth[***]", {})]
    assert report.nodeid == "test_case.py::test_auth[739184]"
    cleanups[0]()


def test_terminal_redactor_can_run_without_a_terminal_reporter():
    session, writer, _, cleanups = terminal_session(enabled=False)
    original = writer.write
    redactor = _TerminalRedactor(lambda item: set())
    redactor.pytest_sessionstart(session)
    assert writer.write is original and not cleanups


@pytest.mark.parametrize(
    "display",
    [
        "tests/test_auth.py::test_login[SECRET123]",
        "test_auth.py::test_login[SECRET123]",
        "test_login[SECRET123]",
    ],
)
def test_xdist_deferral_recognizes_all_display_forms_and_remains_scoped(display, tmp_path):
    session, writer, writes, cleanups = terminal_session(workers=True)
    paths = invocation_paths(tmp_path, "tests")
    session.config.rootpath = paths.rootpath
    session.config.invocation_params = paths.invocation_params
    original = writer.write
    redactor = _TerminalRedactor(lambda item: set())
    redactor.pytest_configure(session.config)
    redactor.pytest_sessionstart(session)
    nodeid = "tests/test_auth.py::test_login[SECRET123]"
    start = redactor.pytest_runtest_logstart(nodeid)
    next(start)
    writer.write(display)
    writer.write("unrelated worker progress")
    with pytest.raises(StopIteration):
        next(start)
    report = pytest.TestReport(
        nodeid=nodeid,
        location=("tests/test_auth.py", 1, "test_login"),
        keywords={},
        outcome="passed",
        longrepr=None,
        when="call",
    )
    report._dolphin_parameter_values = ("SECRET123",)
    redactor.pytest_runtest_logreport(report)
    writer.write("PASSED " + display)
    assert writes == [
        ("unrelated worker progress", {}),
        ("PASSED " + display.replace("SECRET123", "***"), {}),
    ]
    assert report.nodeid == nodeid
    cleanups[0]()
    assert writer.write is original


@pytest.mark.parametrize(
    ("subdir", "expected_path"),
    [
        ("", "tests/nested/test_case.py"),
        ("tests", path_join("nested", "test_case.py")),
        ("tests/nested", "test_case.py"),
        ("sibling", path_join("..", "tests", "nested", "test_case.py")),
    ],
)
def test_display_identifiers_preserve_parameter_suffix(tmp_path, subdir, expected_path):
    config = invocation_paths(tmp_path, subdir)
    nodeid = "tests/nested/test_case.py::TestAuth::test_login[case-a::b/c]"
    assert _display_identifiers(nodeid, config) == (
        nodeid,
        expected_path + "::TestAuth::test_login[case-a::b/c]",
        "b/c]",
    )


@pytest.mark.skipif(not IS_WINDOWS, reason="Different-drive paths require Windows path semantics")
def test_terminal_redacts_absolute_display_path_when_invoked_from_another_drive():
    config = windows_drive_paths()
    nodeid = "tests/test_auth.py::test_login[SECRET123]"
    absolute_display = str(config.rootpath / "tests/test_auth.py") + "::test_login[SECRET123]"
    identifiers = _display_identifiers(nodeid, config)
    assert identifiers == (nodeid, absolute_display, "test_login[SECRET123]")

    redactor = _TerminalRedactor(lambda item: {"SECRET123"})
    redactor._remember(nodeid, {"SECRET123"}, config)
    for identifier in identifiers:
        assert redactor.redact("PASSED " + identifier) == "PASSED " + identifier.replace(
            "SECRET123", "***"
        )
    assert redactor.redact("PASSED test_public") == "PASSED test_public"
