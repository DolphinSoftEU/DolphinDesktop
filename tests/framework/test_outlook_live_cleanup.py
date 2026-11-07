"""Unit tests for the persisted-draft cleanup orchestration of the live Outlook
suite, driven with fakes so they need no Outlook and run in CI.

They prove the draft a quit-with-save persists is always swept -- even when the
"process gone" assertion fails, when reopening the profile skips or errors
(BaseException, not Exception), and when the best-effort sweep's own quit fails
-- and that none of that cleanup masks the original exception.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.office import test_outlook_live_more as more

pytestmark = pytest.mark.unit


class _FakeFolder:
    def __init__(self, subjects: list[str]) -> None:
        self._subjects = subjects

    @property
    def item_count(self) -> int:
        return len(self._subjects)

    def item(self, index: int) -> SimpleNamespace:
        return SimpleNamespace(subject=self._subjects[index - 1])


class _FakeApp:
    def __init__(self, subjects: list[str]) -> None:
        self._subjects = subjects

    def default_folder(self, _name: str) -> _FakeFolder:
        return _FakeFolder(self._subjects)

    def create_mail(self, *, subject: str = "", body: str = "") -> SimpleNamespace:
        return SimpleNamespace(subject=subject, body=body)


@pytest.fixture
def spies(monkeypatch):
    state = {"quits": 0, "best_effort": 0}
    monkeypatch.setattr(more, "_pids", lambda: [])
    monkeypatch.setattr(more, "_start_exclusive_ready", lambda: _FakeApp(["subj"]))
    monkeypatch.setattr(more, "purge_by_subject", lambda app, folder, subject: 1)
    monkeypatch.setattr(
        more,
        "_quit_and_confirm_gone",
        lambda app, **k: state.__setitem__("quits", state["quits"] + 1),
    )
    monkeypatch.setattr(
        more,
        "_best_effort_purge",
        lambda subject: state.__setitem__("best_effort", state["best_effort"] + 1),
    )
    return state


# ---------------------------------------------------------------------------
# _verify_and_purge_persisted: the cleanup umbrella
# ---------------------------------------------------------------------------


def test_happy_path_purges_and_skips_the_best_effort_sweep(spies):
    more._verify_and_purge_persisted("subj")
    assert spies["quits"] == 1
    assert spies["best_effort"] == 0  # the normal purge succeeded


def test_a_nonzero_pid_after_quit_still_triggers_a_sweep(spies, monkeypatch):
    monkeypatch.setattr(more, "_pids", lambda: [1234])  # "process gone" assertion fails
    with pytest.raises(AssertionError):
        more._verify_and_purge_persisted("subj")
    assert spies["best_effort"] == 1  # the persisted draft is still swept


def test_a_skip_while_reopening_sweeps_and_is_reported_as_a_failure(spies, monkeypatch):
    def skip_reopen():
        pytest.skip("another Outlook already running")

    monkeypatch.setattr(more, "_start_exclusive_ready", skip_reopen)
    # The draft was already written to the PST, so a skip here would report a
    # destructive test as "not run": it must surface as a failure instead.
    with pytest.raises(pytest.fail.Exception, match="could not reopen the profile"):
        more._verify_and_purge_persisted("subj")
    assert spies["best_effort"] == 1  # and the persisted draft is still swept


def test_a_failure_before_the_purge_still_triggers_a_sweep(spies, monkeypatch):
    monkeypatch.setattr(more, "_start_exclusive_ready", lambda: _FakeApp([]))  # subject missing
    with pytest.raises(AssertionError):
        more._verify_and_purge_persisted("subj")
    assert spies["quits"] == 1  # the reopened app is quit
    assert spies["best_effort"] == 1  # and the draft is still swept


# ---------------------------------------------------------------------------
# The whole test: a failing first quit must still sweep the persisted draft
# ---------------------------------------------------------------------------


def test_a_failing_first_quit_still_sweeps_the_persisted_draft(monkeypatch):
    swept: list[str] = []
    verified: list[str] = []
    monkeypatch.setattr(more, "_start_exclusive_ready", lambda: _FakeApp(["subj"]))

    def failing_first_quit(app, **kwargs):
        # Outlook has already written the draft to the PST; confirming the exit
        # then times out.
        raise RuntimeError("timeout confirming the process exited")

    monkeypatch.setattr(more, "_quit_and_confirm_gone", failing_first_quit)
    monkeypatch.setattr(more, "_best_effort_purge", lambda subject: swept.append(subject))
    monkeypatch.setattr(
        more, "_verify_and_purge_persisted", lambda subject: verified.append(subject)
    )

    with pytest.raises(RuntimeError, match="timeout confirming"):
        more.test_quit_with_save_changes_persists_unsaved_drafts()

    assert verified == []  # the first quit failed, so verify was never reached
    assert swept == [f"{more._UAT_PREFIX} saved on quit"]  # yet the draft is still swept


# ---------------------------------------------------------------------------
# _best_effort_purge: reports, never raises
# ---------------------------------------------------------------------------


def test_best_effort_reports_instead_of_raising_when_reopen_fails(monkeypatch):
    monkeypatch.setattr(
        more, "_start_exclusive_ready", lambda: (_ for _ in ()).throw(RuntimeError("no profile"))
    )
    quits = []
    monkeypatch.setattr(more, "_quit_and_confirm_gone", lambda app, **k: quits.append(1))
    problem = more._best_effort_purge("subj")
    assert problem is not None and "no profile" in problem
    assert quits == []  # nothing opened, so nothing to quit


def test_best_effort_reports_a_skip_while_reopening(monkeypatch):
    def skip_reopen():
        pytest.skip("another Outlook already running")

    monkeypatch.setattr(more, "_start_exclusive_ready", skip_reopen)
    problem = more._best_effort_purge("subj")  # the skip is captured, not raised
    assert problem is not None and "already running" in problem


def test_best_effort_confirms_removal_even_when_its_own_quit_fails(monkeypatch):
    monkeypatch.setattr(more, "_start_exclusive_ready", lambda: _FakeApp(["subj"]))
    monkeypatch.setattr(more, "purge_by_subject", lambda app, folder, subject: 1)
    monkeypatch.setattr(
        more,
        "_quit_and_confirm_gone",
        lambda app, **k: (_ for _ in ()).throw(RuntimeError("quit hung")),
    )
    # The purge was confirmed; a quit failure is only warned about, and it never
    # replaces the caller's original error.
    with pytest.warns(UserWarning, match="could not quit"):
        assert more._best_effort_purge("subj") is None


# ---------------------------------------------------------------------------
# _sweep_or_report: an unconfirmed sweep after a mutation is never PASS/SKIP
# ---------------------------------------------------------------------------


def test_unconfirmed_sweep_with_nothing_in_flight_fails_the_test(monkeypatch):
    monkeypatch.setattr(more, "_best_effort_purge", lambda subject: "RuntimeError('x')")
    with pytest.raises(pytest.fail.Exception, match="may remain in the PST"):
        more._sweep_or_report("subj")


def test_unconfirmed_sweep_during_a_skip_turns_the_skip_into_a_failure(monkeypatch):
    monkeypatch.setattr(more, "_best_effort_purge", lambda subject: "RuntimeError('x')")
    try:
        pytest.skip("simulated skip in flight")
    except pytest.skip.Exception:
        with pytest.raises(pytest.fail.Exception, match="may remain in the PST"):
            more._sweep_or_report("subj")


def test_unconfirmed_sweep_during_a_real_failure_keeps_it_and_adds_a_note(monkeypatch):
    monkeypatch.setattr(more, "_best_effort_purge", lambda subject: "RuntimeError('x')")
    original = AssertionError("the real failure")
    try:
        raise original
    except AssertionError:
        more._sweep_or_report("subj")  # must not raise or replace the original
    assert any("may remain in the PST" in note for note in original.__notes__)


def test_confirmed_sweep_reports_nothing(monkeypatch):
    monkeypatch.setattr(more, "_best_effort_purge", lambda subject: None)
    more._sweep_or_report("subj")  # no exception, nothing to report


def test_two_failed_profile_opens_fail_the_test_with_the_cleanup_diagnostic(monkeypatch):
    # Both the verification reopen and the best-effort reopen are refused (an
    # Outlook keeps running): the test must FAIL, carrying the cleanup note.
    def skip_reopen():
        pytest.skip("another Outlook already running")

    monkeypatch.setattr(more, "_pids", lambda: [])
    monkeypatch.setattr(more, "_start_exclusive_ready", skip_reopen)
    with pytest.raises(pytest.fail.Exception, match="could not reopen the profile") as info:
        more._verify_and_purge_persisted("subj")
    assert any("may remain in the PST" in note for note in info.value.__notes__)
