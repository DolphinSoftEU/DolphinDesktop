"""Cleanup helpers shared by the destructive live-Outlook suites.

Every item a live test creates must be gone before the test ends, and a cleanup
that silently fails must never let a destructive test report success while
leaving an item behind. These helpers enforce that: they verify the item is
actually gone, and -- when called from a test's ``finally`` -- downgrade a
cleanup failure to a warning only when the test body already failed (so the real
failure is not masked), otherwise they raise.
"""

from __future__ import annotations

import sys
import time
import uuid
import warnings
from typing import Any

# Let a move/delete settle before re-reading a folder's contents (MAPI lags).
_SETTLE = 0.3

# A hidden named property (PS_PUBLIC_STRINGS) carrying a per-draft unique token,
# so a draft can be found in the UAT store without its facade wrapper.
MARKER_PROPERTY = (
    "http://schemas.microsoft.com/mapi/string/"
    "{00020329-0000-0000-C000-000000000046}/DolphinUatDraftMarker"
)
# The marker is set through PropertyAccessor, not registered in the folder's
# UserDefinedProperties, so a DASL filter must name its type explicitly: a
# mapi/string property needs the PT_UNICODE type specifier "/0000001f" appended
# (https://learn.microsoft.com/office/vba/outlook/how-to/search-and-filter/
# filtering-a-custom-field#filtering-custom-properties-referenced-by-the-mapi-string-namespace).
PT_UNICODE_SUFFIX = "/0000001f"


def _entry_ids(folder: Any) -> list[str]:
    """Snapshot a folder's entry ids up front; deleting shifts live indices."""
    return [folder.item(i).entry_id for i in range(1, folder.item_count + 1)]


def _count_with_subject(app: Any, folder: str, subject: str) -> int:
    time.sleep(_SETTLE)
    target = app.default_folder(folder)
    namespace = app._namespace
    total = 0
    for entry_id in _entry_ids(target):
        if str(namespace.GetItemFromID(entry_id, target.store_id).Subject) == subject:
            total += 1
    return total


def _permanently_delete_from_deleted_items(app: Any, subject: str) -> None:
    time.sleep(_SETTLE)
    deleted = app.default_folder("deleted")
    namespace = app._namespace
    for entry_id in _entry_ids(deleted):
        item = namespace.GetItemFromID(entry_id, deleted.store_id)
        if str(item.Subject) == subject:
            item.Delete()  # already in Deleted Items -> permanent


def create_tracked_draft(app: Any, **fields: str) -> Any:
    """Create a draft stamped with a unique UAT marker and save it.

    The marker is a hidden named MAPI property; it travels with the item through
    every move, so ``purge_draft`` can still find and remove the draft after the
    facade wrapper was invalidated (a failed permanent delete or an uncertain
    re-fetch after Move closes it, and its old EntryID may no longer apply).
    """
    mail = app.create_mail(**fields)
    marker = f"dolphin-uat-{uuid.uuid4().hex}"
    mail._com.PropertyAccessor.SetProperty(MARKER_PROPERTY, marker)
    mail.save()
    mail._uat_marker = marker
    return mail


def marker_filter(marker: str) -> str:
    """The DASL ``Items.Restrict`` filter matching items that carry *marker*."""
    return f"@SQL=\"{MARKER_PROPERTY}{PT_UNICODE_SUFFIX}\" = '{marker}'"


def _store_folders(root: Any) -> list[Any]:
    """Every folder of the store under *root*, depth first."""
    found: list[Any] = []
    stack = [root]
    while stack:
        folder = stack.pop()
        found.append(folder)
        subfolders = folder.Folders
        stack.extend(subfolders.Item(i) for i in range(1, subfolders.Count + 1))
    return found


def marked_entry_ids(folder: Any, marker: str) -> list[str]:
    matches = folder.Items.Restrict(marker_filter(marker))
    return [str(matches.Item(i).EntryID) for i in range(1, matches.Count + 1)]


def purge_by_marker(app: Any, marker: str) -> int:
    """Remove every item carrying *marker* from the dedicated UAT store; verify.

    Independent of any facade wrapper. Runs only after re-proving the profile is
    the dedicated, disposable UAT PST (``assert_dedicated_store``), and searches
    only that one store -- every folder of it, so a draft that was moved before
    its wrapper was invalidated is found too. Items outside Deleted Items are
    moved there first, then removed permanently; finally the whole store is
    re-scanned and any remaining marked item raises ``AssertionError``. Returns
    how many marked items were found.
    """
    from tests.office._uat_guard import assert_dedicated_store

    assert_dedicated_store(app)
    namespace = app._namespace
    deleted = app.default_folder("deleted")._com
    store = deleted.Store
    store_id = str(store.StoreID)
    folders = _store_folders(store.GetRootFolder())

    # Snapshot every match first: moving items while scanning would let one
    # item be seen twice (once in its folder, once in Deleted Items).
    deleted_entry = str(deleted.EntryID)
    matches = [
        (str(folder.EntryID) == deleted_entry, entry_id)
        for folder in folders
        for entry_id in marked_entry_ids(folder, marker)
    ]
    for in_deleted, entry_id in matches:
        if not in_deleted:
            namespace.GetItemFromID(entry_id, store_id).Delete()  # -> Deleted Items
    time.sleep(_SETTLE)
    for entry_id in marked_entry_ids(deleted, marker):
        namespace.GetItemFromID(entry_id, store_id).Delete()  # permanent
    time.sleep(_SETTLE)
    leftover = sum(len(marked_entry_ids(folder, marker)) for folder in folders)
    if leftover:
        raise AssertionError(
            f"purge_by_marker left {leftover} item(s) with UAT marker {marker!r} in the UAT store"
        )
    return len(matches)


def _remove_draft(mail: Any) -> None:
    """Remove *mail* for good, through the facade or, failing that, by its marker."""
    marker = getattr(mail, "_uat_marker", None)
    if getattr(mail, "_closed", False):
        # A failed permanent delete or an uncertain re-fetch after Move closes
        # the wrapper while the item may still sit in the PST: go by marker.
        if marker is None:
            raise PermissionError(
                "the draft wrapper is closed and the draft carries no UAT marker, so it "
                "cannot be located; create it with create_tracked_draft()"
            )
        purge_by_marker(mail._owner, marker)
        return
    try:
        if mail.parent_folder.name != mail._owner.default_folder("deleted").name:
            mail.delete()
        mail.delete(permanent=True)
        return
    except Exception as facade_error:
        if marker is None:
            raise
        # The facade path failed (and may have closed the wrapper): remove the
        # item by its marker, then still report the facade failure.
        try:
            purge_by_marker(mail._owner, marker)
        except Exception as marker_error:
            marker_error.add_note(f"the facade cleanup failed first: {facade_error!r}")
            raise
        raise AssertionError(
            f"the facade could not remove the draft ({facade_error!r}); it was removed "
            "by its UAT marker instead"
        ) from facade_error


def purge_draft(mail: Any) -> None:
    """Remove a draft for good, wherever it is now; verify it is gone.

    The facade path (soft delete, then ``delete(permanent=True)``, which confirms
    MAPI_E_NOT_FOUND) is tried first. When the wrapper is already closed, or the
    facade path fails, a draft created by ``create_tracked_draft`` is located by
    its unique marker in the dedicated UAT store and removed independently of
    the wrapper (``purge_by_marker``). When the test body already failed, a
    cleanup problem is only warned about so the real failure is not masked; when
    the body passed, it raises, so a destructive test never reports success with
    a draft left behind -- or with a facade cleanup that silently did not work.
    """
    body_already_failing = sys.exc_info()[0] is not None
    try:
        _remove_draft(mail)
    except Exception as exc:
        if body_already_failing:
            warnings.warn(
                f"Outlook cleanup failed during an already-failing test: {exc!r}",
                stacklevel=2,
            )
            return
        raise AssertionError(f"Outlook test cleanup failed for a draft: {exc!r}") from exc


def purge_calendar_appt(app: Any, entry_id: str, subject: str) -> None:
    """Permanently remove a calendar appointment created by raw COM; confirm gone.

    The facade deletes only owned mail drafts, so appointments are cleaned up
    here: move the item to Deleted Items, permanently remove every Deleted Items
    entry with this (unique) UAT subject, then verify none is left in the
    calendar or Deleted Items. Same masking discipline as ``purge_draft``.

    The only tolerated failure of the initial lookup is an unambiguous
    MAPI_E_NOT_FOUND (the item is already gone); any other COM error propagates
    instead of being swallowed.
    """
    from dolphin_desktop._outlook import _MAPI_E_NOT_FOUND, _com_status_code

    body_already_failing = sys.exc_info()[0] is not None
    try:
        calendar = app.default_folder("calendar")
        try:
            app._namespace.GetItemFromID(entry_id, calendar.store_id).Delete()
        except Exception as exc:
            # Re-raised unless it is the unambiguous "item not found".
            if _com_status_code(exc) != _MAPI_E_NOT_FOUND:
                raise
        _permanently_delete_from_deleted_items(app, subject)
        leftover = _count_with_subject(app, "calendar", subject) + _count_with_subject(
            app, "deleted", subject
        )
        if leftover:
            raise AssertionError(
                f"calendar cleanup left {leftover} item(s) with subject {subject!r}"
            )
    except Exception as exc:
        if body_already_failing:
            warnings.warn(
                f"Outlook calendar cleanup failed during an already-failing test: {exc!r}",
                stacklevel=2,
            )
            return
        raise


def close_and_purge_calendar_appt(app: Any, appt: Any, entry_id: str, subject: str) -> None:
    """Close the appointment wrapper, then always remove the saved appointment.

    ``close()`` can raise a COM error; the purge must still run, because the
    appointment is already saved. When the test body is already failing, a
    close error is downgraded to a warning so the body's original exception is
    the one reported; otherwise the close error propagates (after the purge).
    ``purge_calendar_appt`` itself only warns when an exception is in flight, so
    a failing purge never replaces the original error either.
    """
    body_already_failing = sys.exc_info()[0] is not None
    try:
        appt.close()
    except Exception as close_error:
        if not body_already_failing:
            raise
        warnings.warn(
            f"closing the appointment also failed during an already-failing test: {close_error!r}",
            stacklevel=2,
        )
    finally:
        purge_calendar_appt(app, entry_id, subject)


def purge_by_subject(app: Any, folder: str, subject: str) -> int:
    """Permanently remove every item with *subject* from *folder*; verify none remain.

    Moves matching items in *folder* to Deleted Items, then permanently deletes
    matching items from Deleted Items, and finally re-scans both to confirm the
    subject is gone (the earlier code trusted the two sweeps without verifying
    the permanent delete actually took). Returns how many were removed from
    *folder*.
    """
    namespace = app._namespace
    target = app.default_folder(folder)
    removed = 0
    for entry_id in _entry_ids(target):
        item = namespace.GetItemFromID(entry_id, target.store_id)
        if str(item.Subject) == subject:
            item.Delete()  # to Deleted Items
            removed += 1
    _permanently_delete_from_deleted_items(app, subject)
    leftover = _count_with_subject(app, folder, subject) + _count_with_subject(
        app, "deleted", subject
    )
    if leftover:
        raise AssertionError(
            f"purge_by_subject left {leftover} item(s) with subject {subject!r} behind"
        )
    return removed
