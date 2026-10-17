"""Outlook facade contract: draft-only operations and shared-session ownership."""

from unittest.mock import MagicMock

import pytest

import dolphin_desktop
from dolphin_desktop import OutlookApp, OutlookAppointment, OutlookMail
from dolphin_desktop import _outlook as outlook


@pytest.fixture
def client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(outlook, "_require_win32com", lambda: client)
    return client


def test_public_outlook_facade_is_exported():
    assert "OutlookApp" in dolphin_desktop.__all__
    assert dolphin_desktop.OutlookApp is OutlookApp


@pytest.mark.parametrize("method", ["start", "connect"])
def test_context_does_not_quit_or_logoff_operator_session(client, method):
    with getattr(OutlookApp, method)() as app:
        com = app._com
    com.Quit.assert_not_called()
    com.GetNamespace.return_value.Logoff.assert_not_called()
    com.ActiveInspector.return_value.Close.assert_not_called()


@pytest.mark.parametrize("method", ["start", "connect"])
def test_com_unavailable_is_clear(client, method):
    client.Dispatch.side_effect = OSError("class not registered")
    client.GetActiveObject.side_effect = OSError("not running")
    with pytest.raises(RuntimeError, match=r"classic Outlook|Classic Outlook"):
        getattr(OutlookApp, method)()


def test_mail_fields_attachments_and_local_save(client, tmp_path):
    with OutlookApp.start() as app:
        mail = app.create_mail(to="test@example.invalid", subject="Test", body="plain")
        app._com.CreateItem.assert_called_once_with(0)
        assert isinstance(mail, OutlookMail)
        assert (mail.to, mail.subject, mail.body) == ("test@example.invalid", "Test", "plain")
        mail.cc = "copy@example.invalid"
        mail.bcc = "hidden@example.invalid"
        mail.html_body = "<p>html</p>"
        assert mail.cc == "copy@example.invalid"
        assert mail.bcc == "hidden@example.invalid"
        assert mail.html_body == "<p>html</p>"
        mail._com.EntryID = "entry"
        assert mail.entry_id == "entry"
        attachment = mail.add_attachment(tmp_path / "input.txt")
        mail._com.Attachments.Add.assert_called_once_with(str(tmp_path / "input.txt"), 1)
        attachment._com.FileName = "input.txt"
        assert attachment.name == "input.txt"
        attachment.save_as(tmp_path / "copy.txt")
        attachment._com.SaveAsFile.assert_called_once_with(str(tmp_path / "copy.txt"))
        mail._com.Attachments.Count = 1
        assert mail.attachment_count == 1
        mail.attachment(1)
        mail._com.Attachments.Item.assert_called_once_with(1)
        mail.save_as(tmp_path / "draft.msg")
        mail._com.SaveAs.assert_called_once_with(str(tmp_path / "draft.msg"), 9)
        mail.save()
        mail._com.Save.assert_called_once_with()
    app.quit()
    mail._com.Close.assert_called_once_with(1)
    mail._com.Send.assert_not_called()
    app._com.Quit.assert_not_called()


def test_calendar_fields_and_save(client):
    with OutlookApp.start() as app:
        item = app.create_appointment(subject="Local appointment")
        assert isinstance(item, OutlookAppointment)
        app._com.CreateItem.assert_called_once_with(1)
        item.start = "2030-01-02 10:00"
        item.end = "2030-01-02 11:00"
        item.location = "Desk"
        assert (item.start, item.end, item.location) == (
            "2030-01-02 10:00",
            "2030-01-02 11:00",
            "Desk",
        )
        item.close(save=True)
    item._com.Save.assert_called_once_with()
    item._com.Close.assert_called_once_with(1)
    item._com.Send.assert_not_called()


@pytest.mark.parametrize("name,folder_id", [("INBOX", 6), ("drafts", 16), ("calendar", 9), (5, 5)])
def test_default_folder_lookup_and_foreign_item_ownership(client, name, folder_id):
    with OutlookApp.start() as app:
        folder = app.default_folder(name)
        app._com.GetNamespace.assert_called_once_with("MAPI")
        app._com.GetNamespace.return_value.GetDefaultFolder.assert_called_once_with(folder_id)
        folder._com.Name = "Folder"
        folder._com.Items.Count = 7
        folder._com.Items.Item.return_value.Class = 43
        assert (folder.name, folder.item_count) == ("Folder", 7)
        mail = folder.item(1)
        assert isinstance(mail, OutlookMail)
        folder._com.Items.Item.assert_called_once_with(1)
        folder.folder("Child")
        folder._com.Folders.Item.assert_called_once_with("Child")
    mail._com.Close.assert_not_called()


@pytest.mark.parametrize("item_class,wrapper", [(43, OutlookMail), (26, OutlookAppointment)])
def test_open_local_msg_tracks_only_returned_item(client, tmp_path, item_class, wrapper):
    com = client.Dispatch.return_value
    msg = com.GetNamespace.return_value.OpenSharedItem.return_value
    msg.Class = item_class
    with OutlookApp.start() as app:
        item = app.open_item(tmp_path / "local.msg")
        assert isinstance(item, wrapper)
    com.GetNamespace.return_value.OpenSharedItem.assert_called_once_with(
        str(tmp_path / "local.msg")
    )
    msg.Close.assert_called_once_with(1)
    com.Quit.assert_not_called()


def test_unsupported_item_class_is_rejected(client):
    app = OutlookApp.start()
    folder = app._com.GetNamespace.return_value.GetDefaultFolder.return_value
    folder.Items.Item.return_value.Class = 40
    with pytest.raises(TypeError, match="Unsupported Outlook item class: 40"):
        app.default_folder().item(1)


def test_unsupported_local_item_is_closed_without_touching_session(client, tmp_path):
    app = OutlookApp.start()
    com = app._com.GetNamespace.return_value.OpenSharedItem.return_value
    com.Class = 40
    with pytest.raises(TypeError, match="Unsupported Outlook item class: 40"):
        app.open_item(tmp_path / "unsupported.msg")
    com.Close.assert_called_once_with(1)
    app._com.Quit.assert_not_called()


@pytest.mark.parametrize("send_fails", [False, True])
def test_only_explicit_send_sends_and_failed_send_remains_tracked(client, send_fails):
    app = OutlookApp.start()
    mail = app.create_mail(to="recipient@example.invalid")
    mail._com.Send.assert_not_called()
    if send_fails:
        mail._com.Send.side_effect = OSError("security policy")
        with pytest.raises(OSError, match="security policy"):
            mail.send()
    else:
        mail.send()
    app.quit()
    mail._com.Send.assert_called_once_with()
    assert mail._com.Close.call_count == (1 if send_fails else 0)


@pytest.mark.parametrize("operation", ["Save", "Close"])
def test_cleanup_failure_preserves_item_and_other_items_are_closed(client, operation):
    app = OutlookApp.start()
    app._com.CreateItem.side_effect = [MagicMock(), MagicMock()]
    first, second = app.create_mail(), app.create_mail()
    getattr(first._com, operation).side_effect = OSError("store unavailable")
    with pytest.raises(RuntimeError, match="store unavailable"):
        app.quit(save_changes=True)
    assert app._opened == [first]
    second._com.Close.assert_called_once_with(1)
    if operation == "Save":
        first._com.Close.assert_not_called()
    getattr(first._com, operation).side_effect = None
    app.quit(save_changes=True)
    assert not app._opened
    app._com.Quit.assert_not_called()
