"""Display-only redaction must retain identities, other tests and writer lifetime."""

from __future__ import annotations

import pytest

from dolphin_desktop._terminal import _TerminalRedactor
from tests._allure_redaction import terminal_session


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
def test_xdist_deferral_recognizes_all_display_forms_and_remains_scoped(display):
    session, writer, writes, cleanups = terminal_session(workers=True)
    session.config.cwd_relative_nodeid = lambda nodeid: nodeid.removeprefix("tests/")
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
