"""More real classic-Outlook scenarios on the disposable PST profile (DESKTOP-241).

Complements ``test_outlook_live.py``: recipients and HTML bodies, several
attachments, folder iteration, session-exit persistence, MSG round trips for
appointments and unsupported item classes, and the exclusive-mode guard against
an Outlook that is already running. Same preconditions and the same cleanup
discipline: every item created here is removed before the test ends.
"""

from __future__ import annotations

import subprocess
import sys
import time
import winreg
from datetime import datetime, timedelta

import pytest

from dolphin_desktop import OutlookApp, OutlookAppointment, OutlookMail

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

_IMAGE = "OUTLOOK.EXE"
_UAT_PREFIX = "dolphin uat more"


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


def _outlook_exe() -> str:
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Outlook.Application\CLSID") as key:
        clsid = winreg.QueryValue(key, None)
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32") as key:
        return winreg.QueryValue(key, None).strip('"').split(" /")[0]


@pytest.fixture(autouse=True)
def _require_outlook():
    if not _outlook_registered():
        pytest.skip("classic Outlook (Outlook.Application) is not registered on this machine")
    if not _profiles():
        pytest.skip("no Outlook MAPI profile; create the UAT profile first")


@pytest.fixture
def outlook():
    if _pids():
        pytest.skip("an Outlook is already running; exclusive mode never claims it")
    with OutlookApp.start(exclusive=True) as app:
        yield app
    assert not _pids(), "exclusive Outlook must be gone after quit()"


def _purge(mail: OutlookMail) -> None:
    try:
        if mail.parent_folder.name != mail._owner.default_folder("deleted").name:
            mail.delete()
        mail.delete(permanent=True)
    except Exception:
        pass


def _purge_by_subject(app: OutlookApp, folder: str, subject: str) -> int:
    """Remove items created by an earlier session; returns how many were removed."""
    removed = 0
    namespace = app._namespace
    target = app.default_folder(folder)
    entry_ids = [target.item(i).entry_id for i in range(1, target.item_count + 1)]
    for entry_id in entry_ids:
        item = namespace.GetItemFromID(entry_id, target.store_id)
        if str(item.Subject) == subject:
            item.Delete()  # to Deleted Items
            removed += 1
    deleted = app.default_folder("deleted")
    entry_ids = [deleted.item(i).entry_id for i in range(1, deleted.item_count + 1)]
    for entry_id in entry_ids:
        item = namespace.GetItemFromID(entry_id, deleted.store_id)
        if str(item.Subject) == subject:
            item.Delete()  # permanently
    return removed


def _count(app: OutlookApp, folder: str) -> int:
    time.sleep(0.3)
    return app.default_folder(folder).item_count


# ---------------------------------------------------------------------------
# Recipients, bodies, attachments
# ---------------------------------------------------------------------------


def test_all_recipient_fields_and_html_body_persist(outlook):
    mail = outlook.create_mail(subject=f"{_UAT_PREFIX} recipients")
    try:
        mail.to = "to@example.invalid"
        mail.cc = "cc@example.invalid"
        mail.bcc = "bcc@example.invalid"
        mail.html_body = "<html><body><p>Hello <b>UAT</b></p></body></html>"
        mail.save()

        found = outlook.default_folder("drafts").find_item(mail.entry_id)
        assert found is mail, "an owned draft is looked up as the same wrapper"
        assert found.to == "to@example.invalid"
        assert found.cc == "cc@example.invalid"
        assert found.bcc == "bcc@example.invalid"
        assert "<b>UAT</b>" in found.html_body
        # Outlook derives the plain body from the HTML one.
        assert "Hello UAT" in " ".join(found.body.split())
    finally:
        _purge(mail)


def test_several_attachments_keep_their_order_and_bytes(outlook, tmp_path):
    files = []
    for index in range(3):
        path = tmp_path / f"part{index}.txt"
        path.write_text(f"payload {index}", encoding="utf-8")
        files.append(path)
    mail = outlook.create_mail(subject=f"{_UAT_PREFIX} attachments")
    try:
        for path in files:
            mail.add_attachment(path)
        mail.save()
        assert mail.attachment_count == 3
        assert [mail.attachment(i).name for i in (1, 2, 3)] == [p.name for p in files]
        copy = tmp_path / "copy.txt"
        mail.attachment(2).save_as(copy)
        assert copy.read_text(encoding="utf-8") == "payload 1"
        # attachments are copied by value: the originals may disappear
        files[2].unlink()
        assert mail.attachment(3).name == "part2.txt"
    finally:
        _purge(mail)


def test_add_attachment_with_missing_file_is_a_controlled_error(outlook, tmp_path):
    mail = outlook.create_mail(subject=f"{_UAT_PREFIX} missing attachment")
    try:
        with pytest.raises(Exception):  # noqa: B017 - COM error class varies by Office build
            mail.add_attachment(tmp_path / "nope.txt")
        assert mail.attachment_count == 0
    finally:
        mail.close()


# ---------------------------------------------------------------------------
# Folder iteration and ownership
# ---------------------------------------------------------------------------


def test_folder_items_iterate_in_order_and_reject_index_zero(outlook):
    before = _count(outlook, "drafts")
    created = []
    try:
        for label in ("a", "b", "c"):
            mail = outlook.create_mail(subject=f"{_UAT_PREFIX} iter {label}")
            mail.save()
            created.append(mail)
        drafts = outlook.default_folder("drafts")
        assert drafts.item_count == before + 3
        subjects = {drafts.item(i).subject for i in range(1, drafts.item_count + 1)}
        assert {m.subject for m in created} <= subjects
        assert all(isinstance(drafts.item(i), OutlookMail) for i in range(1, drafts.item_count + 1))
        with pytest.raises(LookupError):
            drafts.item(0)
        with pytest.raises(LookupError):
            drafts.item(drafts.item_count + 1)
    finally:
        for mail in created:
            _purge(mail)
    assert _count(outlook, "drafts") == before


def test_unsaved_draft_closed_without_saving_leaves_no_trace(outlook):
    before = _count(outlook, "drafts")
    mail = outlook.create_mail(subject=f"{_UAT_PREFIX} never saved")
    mail.close()
    assert _count(outlook, "drafts") == before
    with pytest.raises(PermissionError):
        mail.delete()  # closed wrappers lose their move/delete rights


def test_quit_with_save_changes_persists_unsaved_drafts():
    if _pids():
        pytest.skip("an Outlook is already running; exclusive mode never claims it")
    subject = f"{_UAT_PREFIX} saved on quit"
    app = OutlookApp.start(exclusive=True)
    try:
        app.create_mail(subject=subject, body="kept")
    finally:
        app.quit(save_changes=True)
    assert not _pids()

    with OutlookApp.start(exclusive=True) as again:
        try:
            drafts = again.default_folder("drafts")
            subjects = [drafts.item(i).subject for i in range(1, drafts.item_count + 1)]
            assert subject in subjects
        finally:
            assert _purge_by_subject(again, "drafts", subject) == 1


def test_opened_msg_item_cannot_be_moved(outlook, tmp_path):
    msg = tmp_path / "foreign.msg"
    mail = outlook.create_mail(subject=f"{_UAT_PREFIX} foreign")
    try:
        mail.save()
        mail.save_as(msg)
    finally:
        _purge(mail)
    foreign = outlook.open_item(msg)
    try:
        with pytest.raises(PermissionError):
            foreign.move(outlook.default_folder("inbox"))
    finally:
        foreign.close()


# ---------------------------------------------------------------------------
# Appointments and other item classes as MSG files
# ---------------------------------------------------------------------------


def test_appointment_msg_round_trip(outlook, tmp_path):
    msg = tmp_path / "meeting.msg"
    start = datetime(2031, 3, 4, 14, 30)
    appt = outlook.create_appointment(subject=f"{_UAT_PREFIX} appt msg")
    appt.start = start
    appt.end = start + timedelta(minutes=45)
    appt.location = "room 2"
    appt.body = "agenda"
    appt.save_as(msg)
    appt.close()
    assert msg.is_file()

    reopened = outlook.open_item(msg)
    try:
        assert isinstance(reopened, OutlookAppointment)
        assert reopened.subject == f"{_UAT_PREFIX} appt msg"
        assert reopened.location == "room 2"
        assert reopened.body.strip() == "agenda"
        local_start = reopened.start.astimezone()
        assert (local_start.hour, local_start.minute) == (14, 30)
        assert (reopened.end - reopened.start) == timedelta(minutes=45)
    finally:
        reopened.close()


def test_unsupported_item_class_in_msg_is_rejected(outlook, tmp_path):
    """A contact saved as MSG is neither mail nor appointment; the facade says so."""
    msg = tmp_path / "contact.msg"
    contact = outlook._com.CreateItem(2)  # olContactItem, raw COM for setup only
    contact.FullName = "UAT Contact"
    contact.SaveAs(str(msg), 9)
    contact.Close(1)
    assert msg.is_file()
    with pytest.raises(TypeError, match="Unsupported Outlook item class"):
        outlook.open_item(msg)


# ---------------------------------------------------------------------------
# Exclusive-mode guard
# ---------------------------------------------------------------------------


def test_exclusive_start_refuses_when_outlook_is_already_running():
    if _pids():
        pytest.skip("an Outlook is already running; this test starts its own")
    proc = subprocess.Popen([_outlook_exe()])
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not _pids():
            time.sleep(1)
        assert _pids(), "Outlook did not start"
        with pytest.raises(RuntimeError, match=r"OUTLOOK.EXE"):
            OutlookApp.start(exclusive=True)
        assert proc.poll() is None, "the operator's Outlook must survive the refusal"
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
# connect() without Outlook, numeric folder ids, calendar items by index
# ---------------------------------------------------------------------------


def test_connect_without_a_running_outlook_is_a_controlled_error():
    if _pids():
        pytest.skip("an Outlook is running; this test needs none")
    with pytest.raises(RuntimeError, match="No running classic Outlook"):
        OutlookApp.connect()
    assert not _pids(), "a failed connect() must not start Outlook"


def test_default_folders_by_numeric_id_match_their_names(outlook):
    # OlDefaultFolders: 6 inbox, 16 drafts, 5 sent, 3 deleted, 9 calendar
    for number, name in (
        (6, "inbox"),
        (16, "drafts"),
        (5, "sent"),
        (3, "deleted"),
        (9, "calendar"),
    ):
        by_number = outlook.default_folder(number)
        by_name = outlook.default_folder(name)
        assert by_number.entry_id == by_name.entry_id, (number, name)
        assert by_number.store_id == by_name.store_id


def test_calendar_item_by_index_is_an_appointment(outlook):
    appt = outlook.create_appointment(subject=f"{_UAT_PREFIX} indexed appt")
    appt.start = datetime(2032, 5, 6, 9, 0)
    appt.end = datetime(2032, 5, 6, 9, 30)
    appt.save()
    entry = appt.entry_id
    try:
        calendar = outlook.default_folder("calendar")
        matches = [
            calendar.item(i)
            for i in range(1, calendar.item_count + 1)
            if calendar.item(i).entry_id == entry
        ]
        assert len(matches) == 1
        assert isinstance(matches[0], OutlookAppointment)
        assert matches[0].subject == f"{_UAT_PREFIX} indexed appt"
        matches[0].close()
    finally:
        appt.close()
        try:
            outlook._namespace.GetItemFromID(entry).Delete()
        except Exception:
            pass
