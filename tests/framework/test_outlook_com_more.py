"""Contract tests for the Outlook facade paths added with the live suite.

Covers the fresh-proxy rebinding after Move(), the removal check after a
permanent delete, session exit with and without saving, and item creation
field order. All against mocks; the live counterpart is
``tests/office/test_outlook_live.py``.
"""

from unittest.mock import MagicMock, call

import pytest

from dolphin_desktop import OutlookApp
from dolphin_desktop import _outlook as outlook


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


def _draft_com(entry_id="id-1", store_id="store-1", folder_name="Drafts"):
    com = MagicMock()
    com.Sent = False
    com.EntryID = entry_id
    com.Parent.StoreID = store_id
    com.Parent.EntryID = f"{folder_name}-entry"
    com.Parent.Name = folder_name
    return com


def _deleted_folder_com(store_id="store-1"):
    deleted = MagicMock()
    deleted.EntryID = "Deleted-entry"
    deleted.StoreID = store_id
    deleted.Name = "Deleted Items"
    return deleted


# ---------------------------------------------------------------------------
# move(): rebinding to a fresh proxy
# ---------------------------------------------------------------------------


def test_move_rebinds_to_the_item_fetched_by_entry_id_and_store(client):
    com = client.Dispatch.return_value
    namespace = com.GetNamespace.return_value
    draft_com = _draft_com()
    com.CreateItem.return_value = draft_com
    moved = _draft_com(entry_id="id-2", store_id="store-1", folder_name="Target")
    draft_com.Move.return_value = moved
    fresh = _draft_com(entry_id="id-2", store_id="store-1", folder_name="Target")
    namespace.GetItemFromID.return_value = fresh

    with OutlookApp.start() as app:
        draft = app.create_mail(subject="UAT")
        target = app.default_folder("inbox")
        assert draft.move(target) is draft
        namespace.GetItemFromID.assert_called_once_with("id-2", "store-1")
        assert draft._com is fresh, "operations after a move must use the re-fetched proxy"
        assert draft.entry_id == "id-2"


def test_move_keeps_the_returned_proxy_when_the_store_cannot_refetch(client):
    com = client.Dispatch.return_value
    namespace = com.GetNamespace.return_value
    draft_com = _draft_com()
    com.CreateItem.return_value = draft_com
    moved = _draft_com(entry_id="id-2")
    draft_com.Move.return_value = moved
    namespace.GetItemFromID.side_effect = Exception("store does not support GetItemFromID")

    with OutlookApp.start() as app:
        draft = app.create_mail(subject="UAT")
        draft.move(app.default_folder("inbox"))
        assert draft._com is moved


# ---------------------------------------------------------------------------
# delete(permanent=True): fresh proxy + verification
# ---------------------------------------------------------------------------


def _draft_in_deleted_items(client):
    com = client.Dispatch.return_value
    namespace = com.GetNamespace.return_value
    deleted = _deleted_folder_com()
    namespace.GetDefaultFolder.return_value = deleted
    draft_com = _draft_com(entry_id="id-9")
    draft_com.Parent.EntryID = deleted.EntryID  # already in Deleted Items
    draft_com.Parent.StoreID = deleted.StoreID
    draft_com.Parent.Store.GetDefaultFolder.return_value = deleted
    com.CreateItem.return_value = draft_com
    return com, namespace, draft_com


def test_permanent_delete_calls_delete_on_the_fresh_proxy_and_confirms_removal(client):
    _, namespace, draft_com = _draft_in_deleted_items(client)
    fresh = _draft_com(entry_id="id-9")
    calls = []

    def lookup(entry_id, store_id):
        calls.append((entry_id, store_id))
        if len(calls) == 1:
            return fresh  # the re-fetch before Delete()
        raise Exception("The item has been moved or deleted")  # the verification

    namespace.GetItemFromID.side_effect = lookup
    with OutlookApp.start() as app:
        draft = app.create_mail(subject="UAT")
        draft.delete(permanent=True)
        fresh.Delete.assert_called_once_with()
        draft_com.Delete.assert_not_called()
        assert calls == [("id-9", "store-1"), ("id-9", "store-1")]
        assert draft._closed


def test_permanent_delete_falls_back_to_the_own_proxy_when_refetch_fails(client):
    _, namespace, draft_com = _draft_in_deleted_items(client)
    namespace.GetItemFromID.side_effect = Exception("no GetItemFromID")
    with OutlookApp.start() as app:
        draft = app.create_mail(subject="UAT")
        draft.delete(permanent=True)
        draft_com.Delete.assert_called_once_with()
        assert draft._closed


def test_permanent_delete_raises_when_the_item_is_still_there(client):
    _, namespace, draft_com = _draft_in_deleted_items(client)
    namespace.GetItemFromID.return_value = draft_com  # still resolvable after Delete()
    with OutlookApp.start() as app:
        draft = app.create_mail(subject="UAT")
        with pytest.raises(RuntimeError, match="permanent deletion did not take effect"):
            draft.delete(permanent=True)
        assert draft._closed, "the wrapper is not reused after an uncertain delete"


# ---------------------------------------------------------------------------
# Session exit and item creation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("save_changes", [False, True])
def test_quit_closes_created_items_saving_only_when_asked(client, save_changes):
    com = client.Dispatch.return_value
    mail_com = _draft_com()
    appt_com = MagicMock()
    com.CreateItem.side_effect = [mail_com, appt_com]
    app = OutlookApp.start()
    app.create_mail(subject="m")
    app.create_appointment(subject="a")
    app.quit(save_changes=save_changes)
    for item_com in (mail_com, appt_com):
        item_com.Close.assert_called_once_with(1)
        if save_changes:
            item_com.Save.assert_called_once_with()
        else:
            item_com.Save.assert_not_called()
    com.Quit.assert_not_called()  # shared session: the process is never quit


def test_create_mail_sets_recipient_subject_and_body_on_the_new_item(client):
    com = client.Dispatch.return_value
    mail_com = _draft_com()
    com.CreateItem.return_value = mail_com
    with OutlookApp.start() as app:
        mail = app.create_mail(to="a@example.invalid", subject="s", body="b")
        com.CreateItem.assert_called_once_with(0)
        assert (mail_com.To, mail_com.Subject, mail_com.Body) == ("a@example.invalid", "s", "b")
        mail.bcc = "hidden@example.invalid"
        mail.html_body = "<p>x</p>"
        assert mail_com.BCC == "hidden@example.invalid"
        assert mail_com.HTMLBody == "<p>x</p>"
        assert mail in app._opened


def test_find_item_returns_the_owning_wrapper_only_for_the_same_store(client):
    com = client.Dispatch.return_value
    namespace = com.GetNamespace.return_value
    mail_com = _draft_com(entry_id="id-5", store_id="store-A")
    com.CreateItem.return_value = mail_com
    folder = namespace.GetDefaultFolder.return_value
    folder.Items.Count = 1
    same = _draft_com(entry_id="id-5", store_id="store-A")
    same.Class = 43
    folder.Items.Item.return_value = same
    with OutlookApp.start() as app:
        mail = app.create_mail(subject="UAT")
        mail.save()
        assert app.default_folder("drafts").find_item("id-5") is mail
        other_store = _draft_com(entry_id="id-5", store_id="store-B")
        other_store.Class = 43
        folder.Items.Item.return_value = other_store
        found = app.default_folder("drafts").find_item("id-5")
        assert found is not mail
        with pytest.raises(PermissionError):
            found.delete()


def test_attachment_add_uses_by_value_and_resolves_the_path(client, tmp_path):
    com = client.Dispatch.return_value
    mail_com = _draft_com()
    com.CreateItem.return_value = mail_com
    attachment = tmp_path / "a.txt"
    attachment.write_text("x", encoding="utf-8")
    with OutlookApp.start() as app:
        mail = app.create_mail(subject="UAT")
        mail.add_attachment(attachment)
        mail_com.Attachments.Add.assert_called_once_with(str(attachment.resolve()), 1)
        mail_com.Attachments.Count = 1
        assert mail.attachment_count == 1
        mail.attachment(1).save_as(tmp_path / "out.txt")
        assert mail_com.Attachments.Item.call_args == call(1)
        mail_com.Attachments.Item.return_value.SaveAsFile.assert_called_once_with(
            str((tmp_path / "out.txt").resolve())
        )


def test_add_attachment_refuses_a_missing_file_before_touching_com(client, tmp_path):
    com = client.Dispatch.return_value
    mail_com = _draft_com()
    com.CreateItem.return_value = mail_com
    with OutlookApp.start() as app:
        mail = app.create_mail(subject="UAT")
        with pytest.raises(FileNotFoundError):
            mail.add_attachment(tmp_path / "missing.txt")
        mail_com.Attachments.Add.assert_not_called()
