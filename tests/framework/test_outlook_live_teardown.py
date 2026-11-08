"""Unit tests for teardown paths of the live Outlook suites, driven with fakes.

They need no Outlook and run in CI. They force failures in the middle of a
destructive test and prove nothing is left behind: the draft-delete test cleans
up wherever the draft ended up, and an appointment is purged even when closing
its wrapper raises.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.office import _cleanup
from tests.office import test_outlook_live as live

pytestmark = pytest.mark.unit

_FOLDER_NAMES = {"drafts": "Drafts", "deleted": "Deleted Items"}


class _Folder:
    def __init__(self, name: str, store: dict[str, set[str]]) -> None:
        self.name = name
        self._store = store

    @property
    def item_count(self) -> int:
        return len(self._store[self.name])

    def find_item(self, entry_id: str) -> object | None:
        return object() if entry_id in self._store[self.name] else None


class _Mail:
    """A draft that moves between the fake folders like the facade's OutlookMail."""

    def __init__(self, owner: _Outlook) -> None:
        self._owner = owner
        self.entry_id = "entry-1"
        self._where: str | None = None
        self._closed = False
        # create_tracked_draft() stamps its marker through the raw COM item.
        self._com = SimpleNamespace(PropertyAccessor=SimpleNamespace(SetProperty=lambda *a: None))

    @property
    def parent_folder(self) -> SimpleNamespace:
        return SimpleNamespace(name=self._where)

    def save(self) -> None:
        self._where = "Drafts"
        self._owner.store["Drafts"].add(self.entry_id)

    def delete(self, *, permanent: bool = False) -> None:
        if permanent:
            if self._where != "Deleted Items":
                raise ValueError("Permanent deletion requires the draft to be in Deleted Items")
            self._owner.store["Deleted Items"].discard(self.entry_id)
            self._where = None
            return
        if self._where == "Deleted Items":
            raise ValueError("Draft is already in Deleted Items; use explicit permanent=True")
        if self._where is not None:
            self._owner.store[self._where].discard(self.entry_id)
        self._where = "Deleted Items"
        self._owner.store["Deleted Items"].add(self.entry_id)


class _Outlook:
    def __init__(self) -> None:
        self.store: dict[str, set[str]] = {"Drafts": set(), "Deleted Items": set()}
        self.stale_drafts_lookup = False

    def create_mail(self, **_kwargs: str) -> _Mail:
        return _Mail(self)

    def default_folder(self, key: str) -> _Folder:
        folder = _Folder(_FOLDER_NAMES[key], self.store)
        if self.stale_drafts_lookup and key == "drafts":
            folder.find_item = lambda entry_id: object()  # type: ignore[method-assign]
        return folder

    def leftovers(self) -> int:
        return sum(len(ids) for ids in self.store.values())


@pytest.fixture
def fake_outlook(monkeypatch):
    # _count sleeps to let MAPI settle; the fake needs no settling.
    monkeypatch.setattr(live, "_count", lambda app, folder: app.default_folder(folder).item_count)
    return _Outlook()


# ---------------------------------------------------------------------------
# test_draft_delete_moves_to_deleted_items_then_permanent_delete_removes_it
# ---------------------------------------------------------------------------


def test_draft_delete_test_passes_and_leaves_nothing_on_the_happy_path(fake_outlook):
    live.test_draft_delete_moves_to_deleted_items_then_permanent_delete_removes_it(fake_outlook)
    assert fake_outlook.leftovers() == 0


def test_draft_delete_test_cleans_up_after_a_forced_assertion_failure(fake_outlook):
    # The Drafts lookup still "finds" the moved draft: the assertion after the
    # soft delete fails while the draft sits in Deleted Items. The finally must
    # still remove it for good.
    fake_outlook.stale_drafts_lookup = True
    with pytest.raises(AssertionError):
        live.test_draft_delete_moves_to_deleted_items_then_permanent_delete_removes_it(fake_outlook)
    assert fake_outlook.leftovers() == 0


def test_draft_delete_test_cleans_up_when_the_soft_delete_itself_fails(fake_outlook, monkeypatch):
    calls = {"delete": 0}
    real_delete = _Mail.delete

    def failing_first_soft_delete(self, *, permanent=False):
        calls["delete"] += 1
        if calls["delete"] == 1:
            raise RuntimeError("COM refused Move")  # the draft stays in Drafts
        return real_delete(self, permanent=permanent)

    monkeypatch.setattr(_Mail, "delete", failing_first_soft_delete)
    with pytest.raises(RuntimeError, match="COM refused Move"):
        live.test_draft_delete_moves_to_deleted_items_then_permanent_delete_removes_it(fake_outlook)
    assert fake_outlook.leftovers() == 0  # cleaned up from Drafts


# ---------------------------------------------------------------------------
# close_and_purge_calendar_appt
# ---------------------------------------------------------------------------


@pytest.fixture
def purges(monkeypatch):
    calls: list[tuple[object, str, str]] = []
    monkeypatch.setattr(
        _cleanup,
        "purge_calendar_appt",
        lambda app, entry_id, subject: calls.append((app, entry_id, subject)),
    )
    return calls


class _ClosingFails:
    def close(self) -> None:
        raise RuntimeError("COM Close() failed")


class _ClosesFine:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_appointment_is_purged_after_a_normal_close(purges):
    appt = _ClosesFine()
    _cleanup.close_and_purge_calendar_appt("app", appt, "entry", "subj")
    assert appt.closed
    assert purges == [("app", "entry", "subj")]


def test_appointment_is_still_purged_when_close_raises(purges):
    with pytest.raises(RuntimeError, match="COM Close"):
        _cleanup.close_and_purge_calendar_appt("app", _ClosingFails(), "entry", "subj")
    assert purges == [("app", "entry", "subj")]  # the purge ran anyway


def test_close_error_during_a_failing_test_keeps_the_original_error(purges):
    original = AssertionError("the real failure")
    try:
        raise original
    except AssertionError:
        # The body is already failing: the close error is only warned about, so
        # the body's assertion is the error the test reports.
        with pytest.warns(UserWarning, match="closing the appointment also failed"):
            _cleanup.close_and_purge_calendar_appt("app", _ClosingFails(), "entry", "subj")
    assert purges == [("app", "entry", "subj")]
