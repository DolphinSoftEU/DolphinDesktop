"""Unit tests for the wrapper-independent draft cleanup of the live Outlook suites.

The real ``OutlookMail`` facade runs against a small fake COM store (one PST
with Drafts, Inbox and Deleted Items), so these need no Outlook and run in CI.
They force the two cases where the facade invalidates the wrapper while the
item may still be in the PST -- a permanent ``Delete()`` that leaves the item,
and a ``Move()`` whose re-fetch fails -- and prove ``purge_draft`` still finds
the draft by its UAT marker, removes it and confirms nothing is left.
"""

from __future__ import annotations

import itertools
import re

import pytest
import pywintypes

from dolphin_desktop import _outlook
from dolphin_desktop._outlook import OutlookApp, OutlookFolder, OutlookMail
from tests.office import _cleanup, _uat_guard

pytestmark = pytest.mark.unit

MAPI_E_NOT_FOUND = -2147221233
E_ACCESSDENIED = -2147024891
E_INVALIDARG = -2147024809

_TYPED_MARKER_FILTER = re.compile(
    '@SQL="' + re.escape(_cleanup.MARKER_PROPERTY + "/0000001f") + "\" = '([0-9a-z-]+)'"
)


def _com_error(scode: int, text: str) -> pywintypes.com_error:
    return pywintypes.com_error(
        -2147352567, "Exception occurred.", (4096, "Microsoft Outlook", text, None, 0, scode), None
    )


class _Collection:
    def __init__(self, items: list) -> None:
        self._items = list(items)

    @property
    def Count(self) -> int:  # noqa: N802 - COM casing
        return len(self._items)

    def Item(self, index: int):  # noqa: N802
        return self._items[index - 1]


class _Items(_Collection):
    def __init__(self, folder: _Folder) -> None:
        super().__init__(folder.items)

    def Restrict(self, dasl: str) -> _Collection:  # noqa: N802
        # Like Outlook for a property not in UserDefinedProperties: only the
        # exact, typed mapi/string reference is understood; anything else (e.g.
        # the identifier without "/0000001f") is a COM error, not "no match".
        match = _TYPED_MARKER_FILTER.fullmatch(dasl)
        if match is None:
            raise _com_error(E_INVALIDARG, f"Cannot parse condition. Error at {dasl!r}")
        wanted = match.group(1)
        return _Collection([item for item in self._items if item.marker == wanted])


class _Folder:
    def __init__(self, store: _Store, name: str) -> None:
        self.Store = store
        self.StoreID = store.StoreID
        self.Name = name
        self.EntryID = f"folder-{name}"
        self.children: list[_Folder] = []
        self.items: list[_Item] = []

    @property
    def Folders(self) -> _Collection:  # noqa: N802
        return _Collection(self.children)

    @property
    def Items(self) -> _Items:  # noqa: N802
        return _Items(self)

    def add(self, name: str) -> _Folder:
        child = _Folder(self.Store, name)
        self.children.append(child)
        return child


class _Item:
    """A draft that moves like a PST item: a move gives it a new EntryID."""

    Sent = False

    def __init__(self, store: _Store) -> None:
        self._store = store
        self.EntryID = ""
        self.Parent: _Folder | None = None
        self.marker: str | None = None
        self.PropertyAccessor = self
        self.Subject = self.To = self.Body = ""

    def SetProperty(self, name: str, value: str) -> None:  # noqa: N802
        assert name == _cleanup.MARKER_PROPERTY
        self.marker = value

    def Save(self) -> None:  # noqa: N802
        if self.Parent is None:
            self.EntryID = self._store.new_id()
            self.Parent = self._store.drafts
            self._store.drafts.items.append(self)

    def Move(self, target: _Folder) -> _Item:  # noqa: N802
        assert self.Parent is not None
        self.Parent.items.remove(self)
        self.EntryID = self._store.new_id()
        self.Parent = target
        target.items.append(self)
        return self

    def Delete(self) -> None:  # noqa: N802
        store = self._store
        if self.Parent is store.deleted:
            if store.sticky_deletes:
                store.sticky_deletes -= 1  # Outlook silently keeps the item
                return
            store.deleted.items.remove(self)
            self.Parent = None
        else:
            self.Move(store.deleted)


class _Store:
    StoreID = "uat-store"

    def __init__(self) -> None:
        self._ids = itertools.count(1)
        self.sticky_deletes = 0
        self.root = _Folder(self, "Dolphin UAT Store")
        self.drafts = self.root.add("Drafts")
        self.inbox = self.root.add("Inbox")
        self.deleted = self.root.add("Deleted Items")

    def new_id(self) -> str:
        return f"entry-{next(self._ids)}"

    def GetRootFolder(self) -> _Folder:  # noqa: N802
        return self.root

    def GetDefaultFolder(self, folder_id: int) -> _Folder:  # noqa: N802
        assert folder_id == 3  # olFolderDeletedItems
        return self.deleted

    def all_items(self) -> list[_Item]:
        found, stack = [], [self.root]
        while stack:
            folder = stack.pop()
            found.extend(folder.items)
            stack.extend(folder.children)
        return found


class _Namespace:
    def __init__(self, store: _Store) -> None:
        self._store = store
        self.fail_next: Exception | None = None

    def GetItemFromID(self, entry_id: str, store_id: str) -> _Item:  # noqa: N802
        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error
        for item in self._store.all_items():
            if item.EntryID == entry_id and store_id == self._store.StoreID:
                return item
        raise _com_error(MAPI_E_NOT_FOUND, "the specified message could not be found")


class _App:
    """Just enough of OutlookApp; the re-fetch/verification logic is the real one."""

    _fresh_item = OutlookApp._fresh_item
    _refetch_or_same = OutlookApp._refetch_or_same
    _item_still_present = OutlookApp._item_still_present

    def __init__(self) -> None:
        self.store = _Store()
        self._namespace = _Namespace(self.store)
        self._resources: list = []
        self._opened: list = []

    def default_folder(self, key: str) -> OutlookFolder:
        return OutlookFolder(getattr(self.store, key), _owner=self)

    def create_mail(self, *, to: str = "", subject: str = "", body: str = "") -> OutlookMail:
        mail = OutlookMail(_Item(self.store), _owner=self, _created=True)  # type: ignore[arg-type]
        mail.to, mail.subject, mail.body = to, subject, body
        return mail


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(_cleanup, "_SETTLE", 0)
    guarded: list[object] = []
    monkeypatch.setattr(_uat_guard, "assert_dedicated_store", guarded.append)
    instance = _App()
    instance.guarded = guarded
    return instance


def test_tracked_draft_is_saved_with_a_unique_marker(app):
    first = _cleanup.create_tracked_draft(app, subject="a")
    second = _cleanup.create_tracked_draft(app, subject="b")
    assert first._uat_marker != second._uat_marker
    assert [item.marker for item in app.store.drafts.items] == [
        first._uat_marker,
        second._uat_marker,
    ]


# ---------------------------------------------------------------------------
# Regression 1: Delete() leaves the item although the wrapper is now closed
# ---------------------------------------------------------------------------


def test_permanent_delete_that_leaves_the_item_is_purged_by_marker(app):
    mail = _cleanup.create_tracked_draft(app, subject="dolphin uat delete me")
    mail.delete()  # to Deleted Items
    app.store.sticky_deletes = 1  # the next permanent Delete() silently does nothing

    with pytest.raises(RuntimeError, match="kept the draft in Deleted Items"):
        try:
            mail.delete(permanent=True)
        finally:
            assert mail._closed  # the facade invalidated the wrapper ...
            assert app.store.deleted.items  # ... while the item is still there
            _cleanup.purge_draft(mail)

    assert app.store.all_items() == []  # removed independently of the wrapper
    assert app.guarded == [app]  # only after re-proving the dedicated UAT store


def test_closed_wrapper_after_a_failed_delete_is_purged_when_the_body_passed(app):
    mail = _cleanup.create_tracked_draft(app, subject="dolphin uat delete me")
    mail.delete()
    app.store.sticky_deletes = 1
    with pytest.raises(RuntimeError):
        mail.delete(permanent=True)

    _cleanup.purge_draft(mail)  # removal confirmed: nothing to report
    assert app.store.all_items() == []


# ---------------------------------------------------------------------------
# Regression 2: Move() succeeds physically, the re-fetch fails
# ---------------------------------------------------------------------------


def test_move_with_a_failed_refetch_is_purged_by_marker_in_the_target_folder(app):
    mail = _cleanup.create_tracked_draft(app, subject="dolphin uat move me")
    old_entry = mail.entry_id
    app._namespace.fail_next = _com_error(E_ACCESSDENIED, "access is denied")

    with pytest.raises(RuntimeError, match="uncertain"):
        mail.move(app.default_folder("inbox"))
    assert mail._closed
    [moved] = app.store.inbox.items
    assert moved.EntryID != old_entry  # the old identity no longer finds it

    _cleanup.purge_draft(mail)
    assert app.store.all_items() == []


# ---------------------------------------------------------------------------
# The fallback itself
# ---------------------------------------------------------------------------


def test_an_unconfirmed_marker_purge_fails_the_cleanup(app):
    mail = _cleanup.create_tracked_draft(app, subject="dolphin uat stuck")
    mail.delete()
    app.store.sticky_deletes = 10  # every permanent Delete() is ignored
    with pytest.raises(RuntimeError):
        mail.delete(permanent=True)
    with pytest.raises(AssertionError, match="left 1 item"):
        _cleanup.purge_draft(mail)


def test_an_unconfirmed_marker_purge_is_only_warned_during_a_failing_test(app):
    mail = _cleanup.create_tracked_draft(app, subject="dolphin uat stuck")
    mail.delete()
    app.store.sticky_deletes = 10
    try:
        raise AssertionError("the real failure")
    except AssertionError:
        with pytest.raises(RuntimeError):
            mail.delete(permanent=True)
        with pytest.warns(UserWarning, match="left 1 item"):
            _cleanup.purge_draft(mail)


def test_marker_purge_refuses_to_touch_a_store_that_is_not_the_uat_pst(app, monkeypatch):
    mail = _cleanup.create_tracked_draft(app, subject="dolphin uat move me")
    app._namespace.fail_next = _com_error(E_ACCESSDENIED, "access is denied")
    with pytest.raises(RuntimeError):
        mail.move(app.default_folder("inbox"))

    def refuse(_app):
        raise AssertionError("refusing to run destructive Outlook tests -- not the UAT store")

    monkeypatch.setattr(_uat_guard, "assert_dedicated_store", refuse)
    with pytest.raises(AssertionError, match="refusing to run destructive"):
        _cleanup.purge_draft(mail)
    assert len(app.store.inbox.items) == 1  # nothing was deleted outside the guard


def test_a_failing_facade_cleanup_is_reported_even_when_the_marker_purge_works(app, monkeypatch):
    mail = _cleanup.create_tracked_draft(app, subject="dolphin uat facade")

    def broken_delete(*, permanent=False):
        raise RuntimeError("facade delete broke")

    monkeypatch.setattr(mail, "delete", broken_delete)
    with pytest.raises(AssertionError, match="removed by its UAT marker instead"):
        _cleanup.purge_draft(mail)
    assert app.store.all_items() == []  # yet nothing is left behind


def test_a_closed_untracked_draft_cannot_be_located(app):
    mail = app.create_mail(subject="untracked")
    mail._com.Save()
    mail._closed = True
    with pytest.raises(AssertionError, match="create_tracked_draft"):
        _cleanup.purge_draft(mail)


def test_marker_purge_only_matches_its_own_marker(app):
    keep = _cleanup.create_tracked_draft(app, subject="keep")
    drop = _cleanup.create_tracked_draft(app, subject="drop")
    assert _cleanup.purge_by_marker(app, drop._uat_marker) == 1
    assert [item.marker for item in app.store.all_items()] == [keep._uat_marker]


def test_marker_filter_names_the_unicode_type_of_the_undeclared_property():
    # The marker is set via PropertyAccessor and never registered in the
    # folder's UserDefinedProperties, so DASL needs the PT_UNICODE specifier.
    assert _cleanup.marker_filter("dolphin-uat-abc") == (
        '@SQL="http://schemas.microsoft.com/mapi/string/'
        '{00020329-0000-0000-C000-000000000046}/DolphinUatDraftMarker/0000001f"'
        " = 'dolphin-uat-abc'"
    )


def test_the_fake_rejects_an_untyped_marker_filter_like_outlook(app):
    untyped = f"@SQL=\"{_cleanup.MARKER_PROPERTY}\" = 'dolphin-uat-abc'"
    with pytest.raises(pywintypes.com_error):
        app.store.drafts.Items.Restrict(untyped)


def test_facade_status_code_helper_sees_the_fake_not_found():
    # Guards the fake: the real _item_still_present must read it as not-found.
    assert _outlook._com_status_code(_com_error(MAPI_E_NOT_FOUND, "x")) == MAPI_E_NOT_FOUND
