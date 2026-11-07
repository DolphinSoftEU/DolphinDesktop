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


class _SkipLike(BaseException):
    """Stands in for pytest.skip's Skipped: a BaseException, not an Exception."""


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


def test_a_skip_while_reopening_still_triggers_a_sweep_and_propagates(spies, monkeypatch):
    def skip_reopen():
        raise _SkipLike("another Outlook already running")

    monkeypatch.setattr(more, "_start_exclusive_ready", skip_reopen)
    with pytest.raises(_SkipLike):
        more._verify_and_purge_persisted("subj")
    assert spies["best_effort"] == 1


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
# _best_effort_purge: never raises, never masks
# ---------------------------------------------------------------------------


def test_best_effort_warns_instead_of_raising_when_reopen_fails(monkeypatch):
    monkeypatch.setattr(
        more, "_start_exclusive_ready", lambda: (_ for _ in ()).throw(RuntimeError("no profile"))
    )
    quits = []
    monkeypatch.setattr(more, "_quit_and_confirm_gone", lambda app, **k: quits.append(1))
    with pytest.warns(UserWarning, match="did not complete"):
        more._best_effort_purge("subj")
    assert quits == []  # nothing opened, so nothing to quit


def test_best_effort_warns_when_its_own_quit_fails_and_still_returns(monkeypatch):
    monkeypatch.setattr(more, "_start_exclusive_ready", lambda: _FakeApp(["subj"]))
    monkeypatch.setattr(more, "purge_by_subject", lambda app, folder, subject: 1)
    monkeypatch.setattr(
        more,
        "_quit_and_confirm_gone",
        lambda app, **k: (_ for _ in ()).throw(RuntimeError("quit hung")),
    )
    # A quit failure during the sweep must not replace the caller's original
    # error: it is downgraded to a warning and the function returns normally.
    with pytest.warns(UserWarning, match="could not quit"):
        more._best_effort_purge("subj")
