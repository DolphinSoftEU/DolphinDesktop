"""Validate the strict scale precondition, restoration and real-GUI CI gate."""

import pytest

from tests import _display_scale as scaling
from tests._win32_scaled_acceptance import verify_report
from tests.framework.test_ci_security import load


class _Setting:
    minimum = 0
    maximum = 5
    current = 0


class _Display:
    device = "controlled display"

    def __init__(self, percent=100, fail_at=None):
        self.percent = percent
        self.saved_setting = _Setting()
        self.changes = []
        self.fail_at = fail_at

    def read(self):
        return self.percent

    def setting(self):
        return self.saved_setting

    def apply(self, setting, relative):
        self.changes.append((setting, relative))
        if relative == 1 and self.fail_at == "apply":
            raise RuntimeError("transition failure")

    def wait(self, expected):
        if expected == 125 and self.fail_at == "wait":
            raise RuntimeError("transition failure")
        return expected


@pytest.mark.parametrize(("minimum", "maximum", "expected"), [(0, 5, 1), (-1, 4, 0), (-2, 3, -1)])
def test_125_percent_uses_the_recommended_scale_offset(minimum, maximum, expected):
    assert scaling.relative_scale(125, minimum, maximum) == expected


def test_unsupported_scale_cannot_be_reported_as_provisioned():
    with pytest.raises(RuntimeError, match="cannot provide"):
        scaling.relative_scale(125, 0, 0)


@pytest.mark.parametrize("fail", [False, True])
def test_ci_restores_scale_after_success_or_test_failure(monkeypatch, fail):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    display = _Display()
    monkeypatch.setattr(scaling, "DisplayScale", lambda: display)
    evidence = {}
    if fail:
        with pytest.raises(RuntimeError, match="test failure"):
            with scaling.scaled_display(125, evidence):
                raise RuntimeError("test failure")
    else:
        with scaling.scaled_display(125, evidence):
            assert evidence["effective_percent"] == 125
    assert display.changes == [(display.saved_setting, 1), (display.saved_setting, 0)]
    assert evidence["restored_percent"] == 100


def test_local_runs_cannot_change_the_users_display(monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("RUNNER_ENVIRONMENT", raising=False)
    display = _Display()
    monkeypatch.setattr(scaling, "DisplayScale", lambda: display)
    with pytest.raises(RuntimeError, match="disposable GitHub-hosted"):
        with scaling.scaled_display(125, {}):
            pytest.fail("100% must never satisfy the 125% precondition")
    assert not display.changes


def test_already_scaled_display_is_read_only(monkeypatch):
    display = _Display(percent=125)
    monkeypatch.setattr(scaling, "DisplayScale", lambda: display)
    with scaling.scaled_display(125, {}):
        pass
    assert not display.changes


@pytest.mark.parametrize("stage", ["apply", "wait"])
def test_failed_scale_transition_still_restores_original(monkeypatch, stage):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
    display = _Display(fail_at=stage)
    monkeypatch.setattr(scaling, "DisplayScale", lambda: display)
    evidence = {}
    with pytest.raises(RuntimeError, match="transition failure"):
        with scaling.scaled_display(125, evidence):
            pytest.fail("An unverified display must not run acceptance")
    assert display.changes == [(display.saved_setting, 1), (display.saved_setting, 0)]
    assert evidence["restored_percent"] == 100


def test_effective_scaling_must_actually_reach_125_percent():
    with pytest.raises(RuntimeError, match="actual 100%"):
        scaling.DisplayScale.wait(_Display(percent=100), 125, timeout=0)


@pytest.mark.parametrize("actual", [100, 125])
def test_aut_monitor_must_have_the_required_scale(monkeypatch, tmp_path, actual):
    monkeypatch.setenv("DOLPHIN_EXPECT_DISPLAY_SCALE", "125")
    monkeypatch.setattr(scaling, "DisplayScale", lambda hwnd: _Display(percent=actual))
    path = tmp_path / "display-scale.json"
    if actual == 100:
        with pytest.raises(AssertionError, match="Required 125%"):
            scaling.record_window_scale(123, path)
    else:
        scaling.record_window_scale(123, path)
    assert path.is_file()


@pytest.mark.parametrize("outcome", ["skipped", "failure", "error", "missing"])
def test_scaled_acceptance_rejects_incomplete_evidence(tmp_path, outcome):
    report = tmp_path / "results.xml"
    names = [
        "test_native_menu_click_opens_and_reopens_nested_popups",
        "test_winforms_launch_controls_menu_dialog_and_attach[dpi-unaware]",
        "test_winforms_launch_controls_menu_dialog_and_attach[dpi-aware]",
    ]
    if outcome == "missing":
        names.pop()
    tag = "" if outcome == "missing" else f"<{outcome}/>"
    report.write_text(
        "<testsuite>"
        + "".join(f'<testcase name="{name}">{tag}</testcase>' for name in names)
        + "</testsuite>",
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError):
        verify_report(report)


def test_scaled_menu_job_runs_independently_and_gates_packaging():
    jobs = load("ci.yml")["jobs"]
    job = jobs["win32-scaled"]
    assert "needs" not in job  # A unit timeout must not hide the menu evidence.
    assert "if" not in job and "continue-on-error" not in job
    assert "win32-scaled" in jobs["package"]["needs"]
    command = next(step for step in job["steps"] if "125% scaling and run" in step.get("name", ""))
    assert command["run"] == "uv run --no-sync python -m tests._win32_scaled_acceptance"
    assert "if" not in command and "continue-on-error" not in command
