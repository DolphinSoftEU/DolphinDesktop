"""Outlook facade contract: draft-only operations and shared-session ownership."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import dolphin_desktop
from dolphin_desktop import OutlookApp, OutlookAppointment, OutlookMail
from dolphin_desktop import _outlook as outlook


@pytest.mark.parametrize(
    "profile_path",
    [
        r"Software\Microsoft\Office\16.0\Outlook\Profiles",
        r"Software\Microsoft\Office\15.0\Outlook\Profiles",
        r"Software\Microsoft\Windows NT\CurrentVersion\Windows Messaging Subsystem\Profiles",
    ],
)
def test_profile_discovery_reads_supported_current_user_registry_locations(
    monkeypatch, profile_path
):
    context = MagicMock()
    context.__enter__.return_value = "profile-key"

    def open_key(root, path):
        assert root == "HKCU"
        if path != profile_path:
            raise FileNotFoundError(path)
        return context

    registry = SimpleNamespace(
        HKEY_CURRENT_USER="HKCU",
        OpenKey=MagicMock(side_effect=open_key),
        QueryInfoKey=MagicMock(return_value=(2, 0, 0)),
        EnumKey=MagicMock(side_effect=lambda key, index: ["UAT", "Secondary"][index]),
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    assert outlook._configured_profiles() == ["UAT", "Secondary"]
    registry.OpenKey.assert_any_call("HKCU", profile_path)
    registry.QueryInfoKey.assert_called_once_with("profile-key")
    context.__exit__.assert_called_once_with(None, None, None)


def test_profile_discovery_distinguishes_missing_registry_keys_from_access_denied(monkeypatch):
    registry = SimpleNamespace(
        HKEY_CURRENT_USER="HKCU",
        OpenKey=MagicMock(side_effect=FileNotFoundError("not configured")),
    )
    monkeypatch.setitem(sys.modules, "winreg", registry)
    assert outlook._configured_profiles() == []
    error = PermissionError("profile registry inaccessible")
    registry.OpenKey.side_effect = error
    with pytest.raises(
        RuntimeError, match="Cannot inspect configured Outlook MAPI profiles"
    ) as caught:
        outlook._configured_profiles()
    assert caught.value.__cause__ is error


@pytest.fixture
def client(monkeypatch):
    client = MagicMock()
    for com in (client.Dispatch.return_value, client.GetActiveObject.return_value):
        com.GetNamespace.return_value.CurrentProfileName = "UAT"
        com.GetNamespace.return_value.Stores.Count = 1
        com.CreateItem.return_value.Sent = False
    monkeypatch.setattr(outlook, "_require_win32com", lambda: client)
    monkeypatch.setattr(outlook, "_configured_profiles", lambda: ["UAT"])
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
        mail_com, app_com = mail._com, app._com
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
    mail_com.Close.assert_called_once_with(1)
    mail_com.Send.assert_not_called()
    app_com.Quit.assert_not_called()


def test_calendar_fields_and_save(client):
    with OutlookApp.start() as app:
        item = app.create_appointment(subject="Local appointment")
        item_com = item._com
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
    item_com.Save.assert_called_once_with()
    item_com.Close.assert_called_once_with(1)
    item_com.Send.assert_not_called()


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
        mail_com = mail._com
        assert isinstance(mail, OutlookMail)
        folder._com.Items.Item.assert_called_once_with(1)
        folder.folder("Child")
        folder._com.Folders.Item.assert_called_once_with("Child")
    mail_com.Close.assert_not_called()


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
    mail_com = mail._com
    mail._com.Send.assert_not_called()
    if send_fails:
        mail._com.Send.side_effect = OSError("security policy")
        with pytest.raises(OSError, match="security policy"):
            mail.send()
    else:
        mail.send()
    app.quit()
    mail_com.Send.assert_called_once_with()
    assert mail_com.Close.call_count == (1 if send_fails else 0)


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
    client.Dispatch.return_value.Quit.assert_not_called()


class _Folder:
    def __init__(self, name, store):
        self.Name = self.EntryID = name
        self.StoreID = "uat-store"
        self.Store = store
        self.Items = self
        self.Folders = MagicMock()
        self.messages = []

    @property
    def Count(self):  # noqa: N802
        return len(self.messages)

    def Item(self, index):  # noqa: N802
        return self.messages[index - 1]


class _Draft:
    Class = 43
    Sent = False

    def __init__(self, store):
        self.store = store
        self.Parent = store.drafts
        self.EntryID = ""
        self.Subject = self.Body = self.To = ""
        self.invalid = False
        self.closes = 0
        # Outlook quirk reproduced on real PST stores: the proxy returned by
        # Move() ignores Delete(); only a proxy fetched by EntryID deletes.
        self.half_alive = False

    def Save(self):  # noqa: N802
        assert not self.invalid, "stale COM proxy"
        if not self.EntryID:
            self.store.sequence += 1
            self.EntryID = f"id-{self.store.sequence}"
            self.Parent.messages.append(self)

    def Move(self, folder):  # noqa: N802
        assert not self.invalid, "stale COM proxy"
        self.Parent.messages.remove(self)
        moved = _Draft(self.store)
        moved.Subject, moved.Body, moved.To = self.Subject, self.Body, self.To
        moved.Parent = folder
        moved.Save()
        moved.half_alive = True
        self.invalid = True
        return moved

    def Delete(self):  # noqa: N802
        assert self.Parent is self.store.deleted
        assert not self.invalid
        if self.half_alive:
            return  # silently ignored, exactly like Outlook does
        self.Parent.messages.remove(self)
        self.invalid = True

    def Close(self, _):  # noqa: N802
        assert not self.invalid, "stale COM proxy"
        self.closes += 1


class _Store:
    def __init__(self):
        self.sequence = 0
        self.drafts = _Folder("drafts", self)
        self.deleted = _Folder("deleted", self)
        self.target = _Folder("UAT", self)
        self.drafts.Folders.Item.return_value = self.target

    def GetDefaultFolder(self, folder_id):  # noqa: N802
        return {16: self.drafts, 3: self.deleted}[folder_id]

    def GetItemFromID(self, entry_id, store_id=None):  # noqa: N802
        for folder in (self.drafts, self.deleted, self.target):
            for message in folder.messages:
                if message.EntryID == entry_id:
                    message.half_alive = False  # a fresh proxy
                    return message
        raise Exception("The item has been moved or deleted")


def test_owned_draft_move_delete_and_folder_effects(client):
    store = _Store()
    com = client.Dispatch.return_value
    com.CreateItem.side_effect = lambda _: _Draft(store)
    com.GetNamespace.return_value.GetDefaultFolder.side_effect = store.GetDefaultFolder
    com.GetNamespace.return_value.GetItemFromID.side_effect = store.GetItemFromID
    with OutlookApp.start() as app:
        source = app.default_folder("drafts")
        target = source.folder("UAT")
        deleted = app.default_folder("deleted")
        draft = app.create_mail(subject="UAT", body="before")
        draft.save()
        original_id = draft.entry_id
        assert source.find_item(original_id) is draft
        assert source.item_count == 1
        reread = source.item(1)
        reread.body = "after"
        reread.save()
        stale = draft._com
        assert reread.move(target) is draft
        moved_id = draft.entry_id
        assert moved_id != original_id
        assert draft._com is not stale
        assert source.find_item(original_id) is None
        assert source.item_count == 0
        assert target.find_item(moved_id).body == "after"
        assert draft.parent_folder.name == "UAT"
        draft.delete()
        assert target.find_item(moved_id) is None
        assert target.item_count == 0
        assert deleted.item_count == 1
        in_trash = deleted.find_item(draft.entry_id)
        assert in_trash is draft
        with pytest.raises(ValueError, match="already in Deleted Items"):
            in_trash.delete()
        in_trash.delete(permanent=True)
        assert deleted.item_count == 0
        assert deleted.find_item(draft.entry_id) is None
        with pytest.raises(PermissionError):
            draft.delete()


@pytest.mark.parametrize("method", ["move", "delete"])
def test_foreign_and_opened_msg_items_cannot_be_moved_or_deleted(client, tmp_path, method):
    with OutlookApp.start() as app:
        com = app._com.GetNamespace.return_value.OpenSharedItem.return_value
        com.Class = 43
        item = app.open_item(tmp_path / "foreign.msg")
        with pytest.raises(PermissionError, match="created by this OutlookApp"):
            getattr(item, method)(
                app.default_folder("drafts")
            ) if method == "move" else item.delete()
        com.Move.assert_not_called()
        com.Delete.assert_not_called()


def test_move_rejects_cross_session_folder_and_sent_or_closed_draft(client):
    with OutlookApp.start() as app, OutlookApp.start() as other:
        mail = app.create_mail()
        with pytest.raises(PermissionError, match="Destination"):
            mail.move(other.default_folder("drafts"))
        mail._com.Move.assert_not_called()
        mail._com.Sent = True
        with pytest.raises(PermissionError, match="unsent"):
            mail.delete()
        mail.close()
        with pytest.raises(PermissionError, match="live draft"):
            mail.move(app.default_folder("drafts"))


def test_move_with_unknown_outcome_invalidates_proxy_and_prevents_retries(client):
    with OutlookApp.start() as app:
        draft = app.create_mail()
        com = draft._com
        com.Move.return_value = None
        target = app.default_folder("drafts")
        target_com = target._com
        with pytest.raises(RuntimeError, match="Move returned no item"):
            draft.move(target)
        with pytest.raises(PermissionError, match="live draft"):
            draft.move(target)
        with pytest.raises(PermissionError, match="live draft"):
            draft.delete()
    com.Move.assert_called_once_with(target_com)
    com.Close.assert_not_called()
    com.Delete.assert_not_called()


def test_delete_requires_boolean_opt_in_and_rejects_permanent_deletion_outside_trash(client):
    with OutlookApp.start() as app:
        draft = app.create_mail()
        com = draft._com
        com.Parent.EntryID = "drafts"
        com.Parent.StoreID = "store"
        deleted = com.Parent.Store.GetDefaultFolder.return_value
        deleted.EntryID, deleted.StoreID = "deleted", "store"
        with pytest.raises(TypeError, match="permanent must be a bool"):
            draft.delete(permanent="yes")
        with pytest.raises(ValueError, match="requires the draft to be in Deleted Items"):
            draft.delete(permanent=True)
        com.Move.assert_not_called()
        com.Delete.assert_not_called()


def test_folder_identity_preserves_store_context_and_unknown_names_are_rejected(client):
    with OutlookApp.start() as app:
        namespace = app._namespace
        with pytest.raises(ValueError, match="Unknown Outlook default folder"):
            app.default_folder("unknown-folder")
        namespace.GetDefaultFolder.assert_not_called()
        folder = app.default_folder("drafts")
        folder._com.EntryID = "folder-id"
        folder._com.StoreID = "store-id"
        assert (folder.entry_id, folder.store_id) == ("folder-id", "store-id")


def test_lookup_does_not_grant_ownership_for_same_entry_id_in_different_store(client):
    with OutlookApp.start() as app:
        owned = app.create_mail()
        owned._com.EntryID = "same-id"
        owned._com.Parent.StoreID = "owned-store"
        foreign = MagicMock(Class=43, EntryID="same-id")
        foreign.Parent.StoreID = "other-store"
        folder = app.default_folder("drafts")
        folder._com.Items.Item.return_value = foreign
        item = folder.item(1)
        with pytest.raises(PermissionError):
            item.delete()
        foreign.Delete.assert_not_called()


def test_start_rejects_missing_profile_before_dispatch(client, monkeypatch):
    monkeypatch.setattr(outlook, "_configured_profiles", lambda: [])
    with pytest.raises(RuntimeError, match="No configured Outlook MAPI profile"):
        OutlookApp.start()
    client.Dispatch.assert_not_called()


@pytest.mark.parametrize("method", ["start", "connect"])
@pytest.mark.parametrize("failure", ["empty-profile", "no-store", "com-error"])
def test_profile_failures_are_controlled_and_shared_process_is_preserved(client, method, failure):
    com = client.Dispatch.return_value if method == "start" else client.GetActiveObject.return_value
    namespace = com.GetNamespace.return_value
    if failure == "empty-profile":
        namespace.CurrentProfileName = ""
    elif failure == "no-store":
        namespace.Stores.Count = 0
    else:
        com.GetNamespace.side_effect = OSError("MAPI unavailable")
    with pytest.raises(RuntimeError, match="MAPI profile unavailable"):
        getattr(OutlookApp, method)()
    com.Quit.assert_not_called()


def test_profile_validation_failure_preserves_error_when_exclusive_cleanup_also_fails(
    client,
    monkeypatch,
):
    com = client.Dispatch.return_value
    profile_error = OSError("MAPI store unavailable")
    com.GetNamespace.side_effect = profile_error
    session = MagicMock()
    session.quit.side_effect = OSError("shutdown failed")
    monkeypatch.setattr(outlook, "_start_office", lambda *_, **__: (com, session))
    with pytest.raises(RuntimeError, match="MAPI profile unavailable") as caught:
        OutlookApp.start(exclusive=True)
    assert caught.value.__cause__ is profile_error
    assert caught.value.__notes__ == ["Office cleanup also failed: shutdown failed"]
    session.quit.assert_called_once()


def test_unsupported_local_item_preserves_type_error_when_close_also_fails(client, tmp_path):
    with OutlookApp.start() as app:
        com = app._namespace.OpenSharedItem.return_value
        com.Class = 40
        com.Close.side_effect = OSError("item close failed")
        with pytest.raises(TypeError, match="Unsupported Outlook item class: 40"):
            app.open_item(tmp_path / "unsupported.msg")
        com.Close.assert_called_once_with(1)


def test_folder_and_item_errors_are_controlled(client, tmp_path):
    with OutlookApp.start() as app:
        namespace = app._namespace
        namespace.GetDefaultFolder.side_effect = OSError("store offline")
        with pytest.raises(LookupError, match="default folder unavailable"):
            app.default_folder("drafts")
        namespace.GetDefaultFolder.side_effect = None
        folder = app.default_folder("drafts")
        folder._com.Folders.Item.side_effect = OSError("folder missing")
        with pytest.raises(LookupError, match="folder not found"):
            folder.folder("missing")
        folder._com.Items.Item.side_effect = OSError("item missing")
        with pytest.raises(LookupError, match="item not found"):
            folder.item(123)
        namespace.OpenSharedItem.side_effect = OSError("file missing")
        with pytest.raises(LookupError, match="item file unavailable"):
            app.open_item(tmp_path / "missing.msg")


def test_permanent_delete_uses_a_fresh_proxy_and_verifies_removal(client):
    """Delete() on the proxy Move() returned is a no-op on PST stores (seen live)."""
    store = _Store()
    com = client.Dispatch.return_value
    com.CreateItem.side_effect = lambda _: _Draft(store)
    namespace = com.GetNamespace.return_value
    namespace.GetDefaultFolder.side_effect = store.GetDefaultFolder
    namespace.GetItemFromID.side_effect = store.GetItemFromID
    with OutlookApp.start() as app:
        draft = app.create_mail(subject="UAT")
        draft.save()
        draft.delete()
        moved_proxy = draft._com
        assert not moved_proxy.half_alive, "move() must rebind to a fresh EntryID proxy"
        draft.delete(permanent=True)
        assert store.deleted.Count == 0
        assert draft._closed


def test_permanent_delete_reports_when_outlook_keeps_the_item(client):
    store = _Store()
    com = client.Dispatch.return_value
    com.CreateItem.side_effect = lambda _: _Draft(store)
    namespace = com.GetNamespace.return_value
    namespace.GetDefaultFolder.side_effect = store.GetDefaultFolder

    def stubborn_lookup(entry_id, store_id=None):
        item = store.GetItemFromID(entry_id, store_id)
        item.half_alive = True  # make even the "fresh" proxy ignore Delete()
        return item

    namespace.GetItemFromID.side_effect = stubborn_lookup
    with OutlookApp.start() as app:
        draft = app.create_mail(subject="UAT")
        draft.save()
        draft.delete()
        with pytest.raises(RuntimeError, match="permanent deletion did not take effect"):
            draft.delete(permanent=True)
        assert store.deleted.Count == 1
