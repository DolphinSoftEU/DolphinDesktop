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
    hook = redactor.pytest_sessionfinish(session)
    next(hook)
    output = session.config.getoption("xmlpath")
    # The writer runs inside the hook, after its opening yield.
    session.config.rootpath.joinpath(output).write_text(
        '<testsuite tests="1" failures="1" time="1.0">'
        '<testcase name="test_login[739184]" classname="operation" line="1">'
        '<failure message="operation 739184">operation 1 &amp; 739184</failure>tail 739184'
        '<properties><property name="note" value="739184" /></properties>'
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
