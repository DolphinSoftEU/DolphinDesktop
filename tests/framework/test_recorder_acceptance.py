"""The recorder acceptance gate cannot succeed with missing or skipped tests."""

import pytest

from tests._recorder_acceptance import REQUIRED_TESTS, verify_report


@pytest.mark.parametrize("outcome", ["passed", "skipped", "failure", "error", "missing"])
def test_acceptance_requires_every_real_test_to_pass(tmp_path, outcome):
    names = sorted(REQUIRED_TESTS)
    cases = [f'<testcase name="{name}" />' for name in names[1:]]
    if outcome == "passed":
        cases.append(f'<testcase name="{names[0]}" />')
    elif outcome != "missing":
        cases.append(f'<testcase name="{names[0]}"><{outcome} /></testcase>')
    report = tmp_path / "results.xml"
    report.write_text("<testsuites><testsuite>" + "".join(cases) + "</testsuite></testsuites>")
    if outcome == "passed":
        verify_report(report)
    else:
        with pytest.raises(RuntimeError, match=r"incomplete|requires PASS"):
            verify_report(report)
