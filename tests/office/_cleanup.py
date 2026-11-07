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
import warnings
from typing import Any

# Let a move/delete settle before re-reading a folder's contents (MAPI lags).
_SETTLE = 0.3


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


def purge_draft(mail: Any) -> None:
    """Remove a draft for good, wherever it is now; verify it is gone.

    ``delete(permanent=True)`` confirms the item is gone (MAPI_E_NOT_FOUND) and
    raises otherwise, so a failure here is a real leftover. When the test body
    already failed, do not mask it (warn and move on); when the body passed,
    re-raise so a destructive test never reports success with a draft behind.
    """
    body_already_failing = sys.exc_info()[0] is not None
    try:
        if mail.parent_folder.name != mail._owner.default_folder("deleted").name:
            mail.delete()
        mail.delete(permanent=True)
    except Exception as exc:
        if body_already_failing:
            warnings.warn(
                f"Outlook cleanup failed during an already-failing test: {exc!r}",
                stacklevel=2,
            )
            return
        raise AssertionError(f"Outlook test cleanup left a draft behind: {exc!r}") from exc


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
