"""Exercise the publication boundary and reject entity-bearing XML artifacts."""

from __future__ import annotations

import pytest

from dolphin_desktop._junit import _JUnitRedactor
from tests._allure_redaction import junit_session


@pytest.fixture
def session(tmp_path):
    return junit_session(tmp_path)


def test_junit_publication_masks_text_and_preserves_schema_and_verdicts(session):
    redactor = _JUnitRedactor()
    report = pytest.TestReport(
        nodeid="test_login[739184]",
        location=("test_login.py", 1, "test_login"),
        keywords={},
        outcome="failed",
        longrepr="739184",
        when="call",
    )
    report._dolphin_parameter_values = ("739184", "1")
    redactor.pytest_runtest_logreport(report)
    marker_name, token = report.user_properties[-1]
    hook = redactor.pytest_sessionfinish(session)
    next(hook)
    output = session.config.getoption("xmlpath")
    # The writer runs inside the hook, after its opening yield.
    session.config.rootpath.joinpath(output).write_text(
        '<testsuite tests="1" failures="1" time="1.0">'
        '<testcase name="test_login[739184]" classname="operation" line="1">'
        '<failure message="operation 739184">operation 1 &amp; 739184</failure>tail 739184'
        '<properties><property name="note" value="739184" />'
        f'<property name="{marker_name}" value="{token}" /></properties>'
        "</testcase></testsuite>",
        encoding="utf-8",
    )
    with pytest.raises(StopIteration):
        next(hook)
    xml = session.config.rootpath.joinpath(output).read_text(encoding="utf-8")
    assert "739184" not in xml
    assert 'tests="1" failures="1" time="1.0"' in xml
    assert 'classname="operation" line="1"' in xml
    assert "operation *** &amp; ***" in xml
    assert "tail ***" in xml
    assert report.nodeid == "test_login[739184]"
    assert token not in xml and marker_name not in xml


def test_junit_secrets_are_scoped_to_their_test_and_include_custom_attributes(session):
    redactor = _JUnitRedactor()
    report = pytest.TestReport(
        nodeid="test_auth[login]",
        location=("test_auth.py", 1, "test_auth"),
        keywords={},
        outcome="passed",
        longrepr=None,
        when="call",
    )
    report._dolphin_parameter_values = ("login",)
    redactor.pytest_runtest_logreport(report)
    marker_name, token = report.user_properties[-1]
    hook = redactor.pytest_sessionfinish(session)
    next(hook)
    output = session.config.rootpath.joinpath(session.config.getoption("xmlpath"))
    output.write_text(
        '<testsuite name="test_login" tests="2" failures="0">'
        '<testcase name="test_auth[login]" vendor="login" time="1.0">'
        f'<properties><property name="{marker_name}" value="{token}" /></properties>'
        '<system-out custom="login" time="login">login</system-out></testcase>'
        '<testcase name="test_login" vendor="login"><system-out>login</system-out></testcase>'
        "</testsuite>",
        encoding="utf-8",
    )
    with pytest.raises(StopIteration):
        next(hook)
    xml = output.read_text(encoding="utf-8")
    assert '<testsuite name="test_login" tests="2" failures="0">' in xml
    assert '<testcase name="test_auth[***]" vendor="***" time="1.0">' in xml
    assert '<system-out custom="***" time="***">***</system-out>' in xml
    assert '<testcase name="test_login" vendor="login"><system-out>login</system-out>' in xml
    assert marker_name not in xml and token not in xml and "<properties" not in xml


def test_junit_merges_test_phases_and_reuses_serialized_worker_marker():
    worker = _JUnitRedactor()
    controller = _JUnitRedactor()
    tokens = []
    for when, values in (("setup", ("EARLY_VALUE",)), ("call", ()), ("teardown", ("LATE_VALUE",))):
        report = pytest.TestReport(
            nodeid="test_auth[opaque-id]",
            location=("test_auth.py", 1, "test_auth"),
            keywords={},
            outcome="passed",
            longrepr=None,
            when=when,
        )
        report._dolphin_parameter_values = values
        worker.pytest_runtest_logreport(report)
        token = report._dolphin_junit_token
        # Report serialization turns tuples into lists. The controller must
        # retain the worker's opaque identity and not duplicate its property.
        report.user_properties = [list(prop) for prop in report.user_properties]
        report.worker_id = "gw0"
        report.item_index = 0
        controller.pytest_runtest_logreport(report)
        assert len(report.user_properties) == 1
        assert report._dolphin_junit_token == token
        tokens.append(token)
    assert len(set(tokens)) == 1
    assert controller.values[tokens[0]] == {"EARLY_VALUE", "LATE_VALUE"}


@pytest.mark.parametrize(
    "declaration",
    [
        '<!DOCTYPE root [<!ENTITY secret "739184">]>',
        '<!DOCTYPE root SYSTEM "file:///should-never-be-read">',
        '<!DOCTYPE root [<!ENTITY secret SYSTEM "file:///should-never-be-read">]>',
    ],
)
def test_junit_publication_rejects_all_dtds(session, declaration):
    hook = _JUnitRedactor().pytest_sessionfinish(session)
    next(hook)
    output = session.config.rootpath.joinpath(session.config.getoption("xmlpath"))
    output.write_text(declaration + "<root>&secret;</root>", encoding="utf-8")
    with pytest.raises(ValueError, match="DTD is not permitted"):
        next(hook)


def test_junit_publication_without_xml_option_does_not_read_an_artifact(session):
    session.config.getoption = lambda name, default=None: None
    hook = _JUnitRedactor().pytest_sessionfinish(session)
    next(hook)
    with pytest.raises(StopIteration):
        next(hook)


def test_xdist_worker_leaves_publication_to_controller(session):
    session.config.workerinput = {"workerid": "gw0"}
    hook = _JUnitRedactor().pytest_sessionfinish(session)
    next(hook)
    with pytest.raises(StopIteration):
        next(hook)
