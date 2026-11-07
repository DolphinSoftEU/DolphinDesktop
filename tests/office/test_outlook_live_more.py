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
import warnings
import winreg
from datetime import datetime, timedelta

import pytest
from tests.office._cleanup import purge_by_subject, purge_calendar_appt, purge_draft
from tests.office._uat_guard import assert_dedicated_store, uat_store_name

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


def _wait_until_no_process(timeout: float = 30.0) -> bool:
    """A shared-mode instance exits on its own shortly after its last document closes."""
    import time as _time

    deadline = _time.monotonic() + timeout
    while _pids():
        if _time.monotonic() >= deadline:
            return False
        _time.sleep(1)
    return True


def _outlook_exe() -> str:
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Outlook.Application\CLSID") as key:
        clsid = winreg.QueryValue(key, None)
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32") as key:
        return winreg.QueryValue(key, None).strip('"').split(" /")[0]


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


def _confirm_process_gone(pid: int | None, timeout: float = 60.0) -> None:
    """Wait for the exclusive process to exit; force-kill as a last resort."""
    if pid is None:
        return
    deadline = time.monotonic() + timeout
    while pid in _pids():
        if time.monotonic() >= deadline:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            return
        time.sleep(1)


def _quit_and_confirm_gone(app: OutlookApp, *, save_changes: bool = False) -> None:
    """Quit an exclusive Outlook and make sure its process is gone.

    OutlookApp.quit() requests the quit but raises "did not exit after Quit;
    retry cleanup" when the process has not died within the session's short
    wait. An exclusive Outlook driven through many create/delete cycles can
    ignore that single Quit for a long time, so after a short grace period the
    process is force-killed: it is our own disposable UAT process on a throwaway
    PST, so nothing is lost. The call returns only once the pid is really gone.
    """
    pid = app.process_id
    try:
        app.quit(save_changes=save_changes)
    except RuntimeError as exc:
        if "retry cleanup" not in str(exc):
            raise
    if pid is None:
        return
    grace = time.monotonic() + 15
    while pid in _pids():
        if time.monotonic() >= grace:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            break
        time.sleep(1)
    reap = time.monotonic() + 15
    while pid in _pids() and time.monotonic() < reap:
        time.sleep(0.5)


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
        pytest.skip("no Outlook MAPI profile; create the UAT profile first")


@pytest.fixture
def outlook():
    app = _start_exclusive_ready()
    try:
        yield app
    finally:
        _quit_and_confirm_gone(app)
    assert _wait_until_no_process(), "exclusive Outlook must be gone after quit()"


def _count(app: OutlookApp, folder: str) -> int:
    time.sleep(0.3)
    return app.default_folder(folder).item_count


def _best_effort_purge(subject: str) -> None:
    """Try once to remove drafts with *subject*, never raising.

    Used only on an error path where the normal cleanup handle is unavailable,
    and usually while another exception is already propagating, so it must not
    raise anything of its own: every failure -- reopening the profile, the purge
    itself, or the final quit -- is downgraded to a warning, even a quit error
    that would otherwise replace the original reopen failure.
    """
    app = None
    try:
        app = _start_exclusive_ready()
        purge_by_subject(app, "drafts", subject)
    except BaseException as exc:
        warnings.warn(f"best-effort Outlook cleanup did not complete: {exc!r}", stacklevel=2)
    finally:
        if app is not None:
            try:
                _quit_and_confirm_gone(app)
            except BaseException as exc:
                warnings.warn(
                    f"best-effort Outlook cleanup could not quit the process: {exc!r}",
                    stacklevel=2,
                )


def _verify_and_purge_persisted(subject: str) -> None:
    """Reopen the profile, confirm the persisted draft is there, and remove it.

    Everything after the draft was persisted runs here under one cleanup
    umbrella: a failed "process gone" assertion, a ``pytest.skip`` or error while
    reopening (both BaseException), or a failure before the purge all still lead
    to a best-effort sweep, so the persisted draft never leaks into the PST.
    """
    purged = False
    try:
        assert not _pids()
        again = _start_exclusive_ready()
        try:
            drafts = again.default_folder("drafts")
            subjects = [drafts.item(i).subject for i in range(1, drafts.item_count + 1)]
            assert subject in subjects
            assert purge_by_subject(again, "drafts", subject) == 1
            purged = True
        finally:
            _quit_and_confirm_gone(again)
    finally:
        if not purged:
            _best_effort_purge(subject)


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
        purge_draft(mail)


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
        purge_draft(mail)


def test_add_attachment_with_missing_file_is_a_controlled_error(outlook, tmp_path):
    mail = outlook.create_mail(subject=f"{_UAT_PREFIX} missing attachment")
    try:
        # Outlook would silently ignore the missing file; the facade refuses first.
        with pytest.raises(FileNotFoundError):
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
            purge_draft(mail)
    assert _count(outlook, "drafts") == before


def test_unsaved_draft_closed_without_saving_leaves_no_trace(outlook):
    before = _count(outlook, "drafts")
    mail = outlook.create_mail(subject=f"{_UAT_PREFIX} never saved")
    mail.close()
    assert _count(outlook, "drafts") == before
    with pytest.raises(PermissionError):
        mail.delete()  # closed wrappers lose their move/delete rights


def test_quit_with_save_changes_persists_unsaved_drafts():
    subject = f"{_UAT_PREFIX} saved on quit"
    app = _start_exclusive_ready()
    reached_verify = False
    try:
        try:
            app.create_mail(subject=subject, body="kept")
        finally:
            _quit_and_confirm_gone(app, save_changes=True)
        # The draft is now persisted in the PST; _verify_and_purge_persisted owns
        # the reopen/assert/remove and its own cleanup from here on.
        reached_verify = True
        _verify_and_purge_persisted(subject)
    finally:
        # The first quit (save_changes=True) can raise after Outlook already wrote
        # the draft to the PST -- e.g. a timeout confirming the process exited --
        # so if we never reached _verify_and_purge_persisted, sweep here. The
        # best-effort purge never raises, so it cannot mask the original error.
        if not reached_verify:
            _best_effort_purge(subject)


def test_opened_msg_item_cannot_be_moved(outlook, tmp_path):
    msg = tmp_path / "foreign.msg"
    mail = outlook.create_mail(subject=f"{_UAT_PREFIX} foreign")
    try:
        mail.save()
        mail.save_as(msg)
    finally:
        purge_draft(mail)
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
    if not _wait_until_no_process():
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
    if not _wait_until_no_process():
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
    subject = f"{_UAT_PREFIX} indexed appt"
    appt = outlook.create_appointment(subject=subject)
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
        assert matches[0].subject == subject
        matches[0].close()
    finally:
        appt.close()
        purge_calendar_appt(outlook, entry, subject)
