"""Real classic-Outlook COM tests (DESKTOP-241): drafts, folders, MSG files, ownership.

Runs against whatever MAPI profile is the default on the machine. The intended
setup is a dedicated profile with one local PST data file and no mail account
(``tests/fixtures/office/make_outlook_profile.py``): everything below works
offline, nothing can be sent, and no real mailbox is involved. Skips when
classic Outlook is not registered or no profile is configured.

Every draft this module creates is deleted permanently before the test ends.
"""

from __future__ import annotations

import subprocess
import sys
import time
import winreg

import pytest
from tests.office._cleanup import (
    close_and_purge_calendar_appt,
    create_tracked_draft,
    purge_draft,
)
from tests.office._outlook_process import outlook_exe as _outlook_exe
from tests.office._outlook_process import quit_and_confirm_gone as _quit_and_confirm_gone
from tests.office._uat_guard import assert_dedicated_store, uat_store_name

from dolphin_desktop import OutlookApp, OutlookAppointment, OutlookMail

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

_IMAGE = "OUTLOOK.EXE"


def _outlook_registered() -> bool:
    if sys.platform != "win32":
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "Outlook.Application"):
            return True
    except OSError:
        return False


def _profiles() -> list[str]:
    from dolphin_desktop._outlook import _configured_profiles

    return _configured_profiles()


def _pids() -> list[int]:
    from dolphin_desktop._office_session import _office_pids

    return _office_pids(_IMAGE)


def _wait_until_no_process(timeout: float = 30.0) -> bool:
    """A shared-mode instance exits on its own shortly after its last document closes."""
    import time as _time

    deadline = _time.monotonic() + timeout
    while _pids():
        if _time.monotonic() >= deadline:
            return False
        _time.sleep(1)
    return True


def _start_exclusive_ready(attempts: int = 5):
    """Start an exclusive Outlook, retrying the MAPI-not-ready startup race.

    Back-to-back exclusive tests restart OUTLOOK.EXE fast; a freshly started
    process can answer COM before its MAPI namespace has a usable store, which
    surfaces as "MAPI profile unavailable". The profile itself is fine (checked
    in _require_outlook), so retry a few times before giving up.
    """
    import time as _time

    last_exc: Exception | None = None
    for _ in range(attempts):
        if not _wait_until_no_process():
            pytest.skip("an Outlook is already running; exclusive mode never claims it")
        try:
            app = OutlookApp.start(exclusive=True)
            try:
                assert_dedicated_store(app)
            except BaseException:
                app.quit()
                raise
            return app
        except RuntimeError as exc:
            if "MAPI profile unavailable" not in str(exc):
                raise
            last_exc = exc
            _time.sleep(2)
    raise AssertionError(f"Outlook MAPI never became ready after {attempts} starts: {last_exc}")


@pytest.fixture(autouse=True)
def _require_outlook():
    if not _outlook_registered():
        pytest.skip("classic Outlook (Outlook.Application) is not registered on this machine")
    if not uat_store_name():
        pytest.skip(
            "destructive Outlook tests are opt-in: set DOLPHIN_OUTLOOK_UAT to the display "
            "name of a dedicated, disposable test store (a local PST with no mail account, "
            "see tests/fixtures/office/make_outlook_profile.py) to allow them"
        )
    if not _profiles():
        pytest.skip("no Outlook MAPI profile; create the UAT profile first (see module docstring)")


@pytest.fixture
def outlook():
    """An exclusive Outlook process on the default profile; quit at teardown."""
    app = _start_exclusive_ready()
    try:
        yield app
    finally:
        _quit_and_confirm_gone(app)
    assert _wait_until_no_process(), "exclusive Outlook must be gone after quit()"


def _count(app: OutlookApp, folder: str) -> int:
    # Items.Count can lag a moment behind a move/delete; re-read the folder.
    time.sleep(0.3)
    return app.default_folder(folder).item_count


# ---------------------------------------------------------------------------
# Items 1-3, 9: facade, start, folders, clean exit
# ---------------------------------------------------------------------------


def test_facade_is_public_and_starts_an_owned_process(outlook):
    import dolphin_desktop

    assert {"OutlookApp", "OutlookMail", "OutlookFolder"} <= set(dolphin_desktop.__all__)
    assert outlook.owns_process
    assert outlook.process_id in _pids()


def test_default_folders_are_readable(outlook):
    names = {}
    for key in ("inbox", "drafts", "sent", "deleted", "calendar"):
        folder = outlook.default_folder(key)
        names[key] = folder.name
        assert folder.entry_id and folder.store_id
        assert folder.item_count >= 0
    assert len(set(names.values())) == 5, names


# ---------------------------------------------------------------------------
# Items 4-6: draft lifecycle
# ---------------------------------------------------------------------------


def test_draft_create_read_modify_find(outlook):
    before = _count(outlook, "drafts")
    mail = create_tracked_draft(
        outlook, to="uat@example.invalid", subject="dolphin uat", body="hello"
    )
    try:
        mail.save()
        assert _count(outlook, "drafts") == before + 1
        assert mail.parent_folder.name == outlook.default_folder("drafts").name
        assert mail.entry_id

        mail.subject = "dolphin uat edited"
        mail.cc = "second@example.invalid"
        mail.save()

        found = outlook.default_folder("drafts").find_item(mail.entry_id)
        assert isinstance(found, OutlookMail)
        assert found.subject == "dolphin uat edited"
        assert found.body.strip() == "hello"
        assert found.to == "uat@example.invalid"
        assert found.cc == "second@example.invalid"
    finally:
        purge_draft(mail)
    assert _count(outlook, "drafts") == before


def test_draft_delete_moves_to_deleted_items_then_permanent_delete_removes_it(outlook):
    drafts_before = _count(outlook, "drafts")
    deleted_before = _count(outlook, "deleted")
    mail = create_tracked_draft(outlook, subject="dolphin uat delete me")
    # Every step after create_mail() is covered: if any assertion or delete
    # fails, the finally removes the draft from wherever it is (Drafts or
    # Deleted Items). Only a confirmed permanent delete skips the cleanup.
    permanently_deleted = False
    try:
        mail.save()
        entry = mail.entry_id

        mail.delete()
        assert mail.parent_folder.name == outlook.default_folder("deleted").name
        assert _count(outlook, "drafts") == drafts_before
        assert _count(outlook, "deleted") == deleted_before + 1
        assert outlook.default_folder("drafts").find_item(entry) is None

        with pytest.raises(ValueError, match="already in Deleted Items"):
            mail.delete()  # a second soft delete never silently turns permanent

        mail.delete(permanent=True)  # verifies MAPI_E_NOT_FOUND or raises
        permanently_deleted = True
        assert _count(outlook, "deleted") == deleted_before
    finally:
        if not permanently_deleted:
            purge_draft(mail)


def test_draft_can_be_moved_between_default_folders(outlook):
    mail = create_tracked_draft(outlook, subject="dolphin uat move me")
    mail.save()
    try:
        inbox = outlook.default_folder("inbox")
        inbox_before = inbox.item_count
        mail.move(inbox)
        assert mail.parent_folder.name == inbox.name
        assert _count(outlook, "inbox") == inbox_before + 1
        mail.move(outlook.default_folder("drafts"))
        assert mail.parent_folder.name == outlook.default_folder("drafts").name
        assert _count(outlook, "inbox") == inbox_before
    finally:
        purge_draft(mail)


def test_permanent_delete_outside_deleted_items_is_refused(outlook):
    mail = create_tracked_draft(outlook, subject="dolphin uat guard")
    mail.save()
    try:
        with pytest.raises(ValueError, match="requires the draft to be in Deleted Items"):
            mail.delete(permanent=True)
        assert mail.parent_folder.name == outlook.default_folder("drafts").name
    finally:
        purge_draft(mail)


# ---------------------------------------------------------------------------
# MSG files, attachments, appointments
# ---------------------------------------------------------------------------


def test_save_as_msg_and_reopen_without_gaining_ownership(outlook, tmp_path):
    msg = tmp_path / "draft.msg"
    attachment = tmp_path / "note.txt"
    attachment.write_text("attached", encoding="utf-8")
    mail = create_tracked_draft(outlook, subject="dolphin uat msg", body="persisted")
    try:
        mail.add_attachment(attachment)
        assert mail.attachment_count == 1
        assert mail.attachment(1).name == "note.txt"
        mail.save()
        mail.save_as(msg)
    finally:
        purge_draft(mail)
    assert msg.is_file() and msg.stat().st_size > 0

    reopened = outlook.open_item(msg)
    assert isinstance(reopened, OutlookMail)
    assert reopened.subject == "dolphin uat msg"
    assert reopened.body.strip() == "persisted"
    assert reopened.attachment_count == 1
    saved_copy = tmp_path / "note-copy.txt"
    reopened.attachment(1).save_as(saved_copy)
    assert saved_copy.read_text(encoding="utf-8") == "attached"
    # A file-backed item is not a draft this session created: no move/delete.
    with pytest.raises(PermissionError):
        reopened.delete()
    reopened.close()


def test_appointment_round_trip(outlook):
    from datetime import datetime, timedelta

    subject = "dolphin uat appt"
    start = datetime(2030, 1, 15, 10, 0)
    appt = outlook.create_appointment(subject=subject)
    appt.start = start
    appt.end = start + timedelta(hours=1)
    appt.location = "room 1"
    appt.save()
    # Capture the entry id right after save(), before any assertion can fail:
    # cleanup in finally must never hit an unbound name on an early failure.
    entry = appt.entry_id
    try:
        assert isinstance(appt, OutlookAppointment)
        found = outlook.default_folder("calendar").find_item(entry)
        assert isinstance(found, OutlookAppointment)
        assert found.subject == subject
        assert found.location == "room 1"
        # pywin32 hands back aware datetimes in UTC; Outlook stored local 10:00.
        local_start = found.start.astimezone()
        assert (local_start.hour, local_start.minute) == (10, 0)
        assert (found.end - found.start).total_seconds() == 3600
        found.close()
    finally:
        # The facade deletes only owned mail drafts, so the calendar entry is
        # removed here -- permanently, and verified gone. The purge runs even if
        # closing the wrapper raises; a leftover fails the test unless the body
        # (or the close) already did, whose error is then the one reported.
        close_and_purge_calendar_appt(outlook, appt, entry, subject)


# ---------------------------------------------------------------------------
# Item 8: attach to a running (visible) Outlook without owning it
# ---------------------------------------------------------------------------


def test_connect_attaches_to_a_running_outlook_and_never_quits_it():
    if not _wait_until_no_process():
        pytest.skip("an Outlook is already running; this test starts its own")
    exe = _outlook_exe()
    proc = subprocess.Popen([exe])
    try:
        app = None
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                app = OutlookApp.connect()
                break
            except RuntimeError:
                time.sleep(2)
        if app is None:
            pytest.skip("Outlook did not register as the active COM object within 120 s")
        assert not app.owns_process
        assert app.process_id is None
        assert_dedicated_store(app)
        drafts = app.default_folder("drafts")
        count = drafts.item_count
        mail = create_tracked_draft(app, subject="dolphin uat via connect")
        mail.save()
        try:
            assert _count(app, "drafts") == count + 1
        finally:
            purge_draft(mail)
        app.quit()
        assert proc.poll() is None, (
            "quit() on a connected session must not end the operator's Outlook"
        )
    finally:
        try:
            import win32com.client as client

            client.GetActiveObject("Outlook.Application").Quit()
        except Exception:
            pass
        for _ in range(60):
            if proc.poll() is not None:
                break
            time.sleep(1)
        if proc.poll() is None:
            proc.kill()


# ---------------------------------------------------------------------------
# Item 10: controlled errors
# ---------------------------------------------------------------------------


def test_controlled_errors(outlook, tmp_path):
    with pytest.raises(ValueError, match="Unknown Outlook default folder"):
        outlook.default_folder("spam-folder")
    with pytest.raises(LookupError, match="folder not found"):
        outlook.default_folder("inbox").folder("no-such-subfolder")
    with pytest.raises(LookupError, match="item not found"):
        outlook.default_folder("drafts").item(10_000)
    with pytest.raises(LookupError, match="item file unavailable"):
        outlook.open_item(tmp_path / "missing.msg")
    assert outlook.default_folder("drafts").find_item("not-an-entry-id") is None
