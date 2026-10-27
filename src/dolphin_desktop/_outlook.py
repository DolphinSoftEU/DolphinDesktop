"""Classic Outlook COM facade; never terminate the shared Outlook session."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ._office import _require_win32com
from ._office_session import _OfficeSession, _OfficeStartupError, _start_office

_FOLDERS = {"inbox": 6, "drafts": 16, "sent": 5, "deleted": 3, "calendar": 9}


def _configured_profiles() -> list[str]:
    """Read profile names before COM activation can display first-run setup."""
    import winreg

    profiles: list[str] = []
    for path in (
        r"Software\Microsoft\Office\16.0\Outlook\Profiles",
        r"Software\Microsoft\Office\15.0\Outlook\Profiles",
        r"Software\Microsoft\Windows NT\CurrentVersion\Windows Messaging Subsystem\Profiles",
    ):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                profiles.extend(winreg.EnumKey(key, n) for n in range(winreg.QueryInfoKey(key)[0]))
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RuntimeError("Cannot inspect configured Outlook MAPI profiles") from exc
    return profiles


class OutlookAttachment:
    """A mail attachment that can be saved to an explicit local path."""

    def __init__(self, _com: Any, *, _owner: OutlookApp | None = None) -> None:
        self._com = _com
        if _owner is not None:
            _owner._resources.append(self)

    @property
    def name(self) -> str:
        """Original attachment file name."""
        return str(self._com.FileName)

    def save_as(self, path: str | Path) -> None:
        """Save attachment bytes to the caller's chosen path."""
        self._com.SaveAsFile(str(Path(path).resolve()))


class OutlookItem:
    """Common mail/calendar fields and explicit persistence operations."""

    def __init__(
        self,
        _com: Any,
        *,
        _owner: OutlookApp | None = None,
        _created: bool = False,
    ) -> None:
        self._com = _com
        self._owner = _owner
        self._created = _created
        self._closed = False
        if _owner is not None:
            _owner._resources.append(self)

    @property
    def subject(self) -> str:
        """Item subject."""
        return str(self._com.Subject)

    @subject.setter
    def subject(self, value: str) -> None:
        self._com.Subject = value

    @property
    def body(self) -> str:
        """Plain-text body."""
        return str(self._com.Body)

    @body.setter
    def body(self, value: str) -> None:
        self._com.Body = value

    @property
    def entry_id(self) -> str:
        """Store identifier (empty for a new unsaved item)."""
        return str(self._com.EntryID or "")

    def save(self) -> None:
        """Persist to the current Outlook store; never send mail or invitations."""
        self._com.Save()

    def save_as(self, path: str | Path) -> None:
        """Save a local Unicode MSG file without sending the item."""
        self._com.SaveAs(str(Path(path).resolve()), 9)  # olMSGUnicode

    def close(self, *, save: bool = False) -> None:
        """Close the item, optionally saving; failed saves leave it open."""
        if self._closed:
            return
        if save:
            self.save()
        self._com.Close(1)  # olDiscard; already saved explicitly above
        self._closed = True


class OutlookMail(OutlookItem):
    """A mail message with recipients, body and attachment access."""

    def _require_owned_draft(self) -> OutlookApp:
        if self._owner is None or not self._created or self._closed:
            raise PermissionError("Move/delete requires a live draft created by this OutlookApp")
        if self._com.Sent:
            raise PermissionError("Move/delete is restricted to unsent drafts")
        return self._owner

    @property
    def parent_folder(self) -> OutlookFolder:
        """Current containing folder (after save/move), without transferring ownership."""
        return OutlookFolder(self._com.Parent, _owner=self._owner)

    def move(self, folder: OutlookFolder) -> OutlookMail:
        """Move an owned draft and keep this wrapper bound to the returned COM item.

        Only folders obtained from the creating OutlookApp are accepted. Existing
        mailbox items and locally opened MSG files cannot be moved or deleted.
        """
        owner = self._require_owned_draft()
        if not isinstance(folder, OutlookFolder) or folder._owner is not owner:
            raise PermissionError("Destination must belong to the draft's OutlookApp")
        moved = self._com.Move(folder._com)
        if moved is None:
            self._closed = True  # outcome unknown; never reuse a potentially stale proxy
            raise RuntimeError("Outlook Move returned no item; verify the destination folder")
        # The proxy Move() returns is only half alive on PST stores: Delete() on
        # it silently does nothing. Re-fetch the item by its new EntryID so every
        # later operation acts on a proxy Outlook fully honours.
        self._com = owner._fresh_item(moved)
        return self

    def delete(self, *, permanent: bool = False) -> None:
        """Delete an owned draft; permanent deletion requires explicit opt-in.

        Normal deletion moves to that store's Deleted Items folder, retaining
        the returned item for verification/cleanup. Repeated normal deletion
        never silently becomes permanent. permanent=True is allowed only there.
        """
        owner = self._require_owned_draft()
        if not isinstance(permanent, bool):
            raise TypeError("permanent must be a bool")
        parent = self._com.Parent
        deleted = parent.Store.GetDefaultFolder(3)  # olFolderDeletedItems in this store
        in_deleted = parent.EntryID == deleted.EntryID and parent.StoreID == deleted.StoreID
        if permanent:
            if not in_deleted:
                raise ValueError("Permanent deletion requires the draft to be in Deleted Items")
            entry_id, store_id = str(self._com.EntryID), str(parent.StoreID)
            owner._fresh_item(self._com).Delete()
            self._closed = True
            if owner._item_exists(entry_id, store_id):
                raise RuntimeError(
                    "Outlook kept the draft in Deleted Items after Delete(); "
                    "permanent deletion did not take effect"
                )
        else:
            if in_deleted:
                raise ValueError("Draft is already in Deleted Items; use explicit permanent=True")
            self.move(OutlookFolder(deleted, _owner=owner))

    @property
    def to(self) -> str:
        """Semicolon-separated recipient names or addresses."""
        return str(self._com.To)

    @to.setter
    def to(self, value: str) -> None:
        self._com.To = value

    @property
    def cc(self) -> str:
        """Carbon-copy recipients."""
        return str(self._com.CC)

    @cc.setter
    def cc(self, value: str) -> None:
        self._com.CC = value

    @property
    def bcc(self) -> str:
        """Blind-carbon-copy recipients."""
        return str(self._com.BCC)

    @bcc.setter
    def bcc(self, value: str) -> None:
        self._com.BCC = value

    @property
    def html_body(self) -> str:
        """HTML message body."""
        return str(self._com.HTMLBody)

    @html_body.setter
    def html_body(self, value: str) -> None:
        self._com.HTMLBody = value

    @property
    def attachment_count(self) -> int:
        """Number of attachments."""
        return int(self._com.Attachments.Count)

    def attachment(self, index: int) -> OutlookAttachment:
        """Return an attachment by 1-based index."""
        return OutlookAttachment(self._com.Attachments.Item(index), _owner=self._owner)

    def add_attachment(self, path: str | Path) -> OutlookAttachment:
        """Attach a local file by value."""
        return OutlookAttachment(
            self._com.Attachments.Add(str(Path(path).resolve()), 1),
            _owner=self._owner,
        )

    def send(self) -> None:
        """Explicitly send this message using Outlook's account and security policy.

        No other method sends mail. Object Model Guard may prompt or reject
        this operation; its restrictions are not bypassed.
        """
        self._com.Send()
        self._closed = True


class OutlookAppointment(OutlookItem):
    """A calendar appointment; saving does not send meeting invitations."""

    @property
    def start(self) -> datetime:
        """Start time using Outlook's local time semantics."""
        return self._com.Start

    @start.setter
    def start(self, value: datetime) -> None:
        self._com.Start = value

    @property
    def end(self) -> datetime:
        """End time using Outlook's local time semantics."""
        return self._com.End

    @end.setter
    def end(self, value: datetime) -> None:
        self._com.End = value

    @property
    def location(self) -> str:
        """Appointment location."""
        return str(self._com.Location)

    @location.setter
    def location(self, value: str) -> None:
        self._com.Location = value


def _wrap_item(com: Any, *, owner: OutlookApp | None = None) -> OutlookItem:
    if com.Class == 43:  # olMail
        return OutlookMail(com, _owner=owner)
    if com.Class == 26:  # olAppointment
        return OutlookAppointment(com, _owner=owner)
    raise TypeError(f"Unsupported Outlook item class: {com.Class}")


class OutlookFolder:
    """Outlook folder with 1-based, read-only item lookup."""

    def __init__(self, _com: Any, *, _owner: OutlookApp | None = None) -> None:
        self._com = _com
        self._owner = _owner
        if _owner is not None:
            _owner._resources.append(self)

    @property
    def name(self) -> str:
        """Folder display name."""
        return str(self._com.Name)

    @property
    def entry_id(self) -> str:
        """Folder EntryID, unique within its store."""
        return str(self._com.EntryID)

    @property
    def store_id(self) -> str:
        """Containing store's identifier."""
        return str(self._com.StoreID)

    @property
    def item_count(self) -> int:
        """Number of items, including classes outside the mail/calendar API."""
        return int(self._com.Items.Count)

    def item(self, index: int) -> OutlookItem:
        """Return mail/appointment by 1-based index; reject unsupported classes."""
        try:
            com = self._com.Items.Item(index)
        except Exception as exc:
            raise LookupError(f"Outlook item not found at index {index}") from exc
        return self._owner._lookup_item(com) if self._owner is not None else _wrap_item(com)

    def find_item(self, entry_id: str) -> OutlookItem | None:
        """Find mail/appointment by EntryID in this folder; return None when absent."""
        for index in range(1, self.item_count + 1):
            com = self._com.Items.Item(index)
            if com.EntryID == entry_id:
                return self._owner._lookup_item(com) if self._owner is not None else _wrap_item(com)
        return None

    def folder(self, name_or_index: str | int) -> OutlookFolder:
        """Return a subfolder by name or 1-based index."""
        try:
            com = self._com.Folders.Item(name_or_index)
        except Exception as exc:
            raise LookupError(f"Outlook folder not found: {name_or_index}") from exc
        return OutlookFolder(com, _owner=self._owner)


class OutlookApp:
    """COM API for classic Outlook with an existing configured MAPI profile.

    No implicit sending, logon or logoff occurs. Context exit closes owned items.
    Shared sessions are preserved; exclusive sessions also quit their own process.
    New Outlook does not provide this COM object model.
    """

    def __init__(self, _com: Any, *, _session: _OfficeSession | None = None) -> None:
        self._com = _com
        self._session = _session
        self._opened: list[OutlookItem] = []
        self._namespace: Any = None
        self._resources: list[Any] = []

    @property
    def owns_process(self) -> bool:
        """Whether this exclusive session still owns a process awaiting confirmed exit."""
        return self._session is not None and not self._session.closed

    @property
    def process_id(self) -> int | None:
        """Verified exclusive process ID, or None for shared/connected sessions."""
        return self._session.pid if self._session is not None else None

    def _validate_profile(self) -> None:
        try:
            namespace = self._com.GetNamespace("MAPI")
            if not namespace.CurrentProfileName or namespace.Stores.Count < 1:
                raise ValueError("No active profile or accessible store")
            self._namespace = namespace
        except Exception as exc:
            raise RuntimeError("Outlook MAPI profile unavailable or no accessible store") from exc

    def _fresh_item(self, com: Any) -> Any:
        """Re-fetch *com* by EntryID; fall back to *com* when the store cannot."""
        try:
            return self._namespace.GetItemFromID(str(com.EntryID), str(com.Parent.StoreID))
        except Exception:
            return com

    def _item_exists(self, entry_id: str, store_id: str) -> bool:
        try:
            self._namespace.GetItemFromID(entry_id, store_id)
        except Exception:
            return False
        return True

    def _lookup_item(self, com: Any) -> OutlookItem:
        # Only drafts created here may regain ownership after a folder lookup.
        # EntryID alone is insufficient: it must refer to the same store too.
        for owned in self._opened:
            if owned._created and not owned._closed and com.EntryID:
                if (
                    com.EntryID == owned.entry_id
                    and com.Parent.StoreID == owned._com.Parent.StoreID
                ):
                    return owned
        return _wrap_item(com, owner=self)

    @classmethod
    def connect(cls) -> OutlookApp:
        """Attach to running classic Outlook or raise RuntimeError."""
        try:
            com = _require_win32com().GetActiveObject("Outlook.Application")
        except Exception as exc:
            raise RuntimeError("No running classic Outlook instance found.") from exc
        app = cls(com)
        app._validate_profile()
        return app

    @classmethod
    def start(cls, *, exclusive: bool = False) -> OutlookApp:
        """Start/reuse classic Outlook; exclusive=True owns an isolated UAT process.

        A configured profile is required before COM activation. The resulting
        MAPI namespace must also have an active profile and accessible store.
        """
        if not _configured_profiles():
            raise RuntimeError("No configured Outlook MAPI profile; configure a test profile first")
        try:
            com, session = _start_office(
                _require_win32com(),
                "Outlook.Application",
                "OUTLOOK.EXE",
                exclusive=exclusive,
            )
        except _OfficeStartupError:
            raise
        except Exception as exc:
            raise RuntimeError(
                "Classic Outlook COM is unavailable; install classic Outlook and "
                "configure a MAPI profile. Exclusive mode requires no existing OUTLOOK.EXE. "
                "New Outlook is not supported by COM."
            ) from exc
        app = cls(com, _session=session)
        try:
            app._validate_profile()
        except BaseException as exc:
            try:
                app.quit()
            except Exception as cleanup_error:
                exc.add_note(f"Office cleanup also failed: {cleanup_error}")
            raise
        return app

    def default_folder(self, name_or_id: str | int = "inbox") -> OutlookFolder:
        """Get inbox/drafts/sent/deleted/calendar or an OlDefaultFolders integer."""
        try:
            folder_id = _FOLDERS[name_or_id.lower()] if isinstance(name_or_id, str) else name_or_id
        except KeyError as exc:
            raise ValueError(f"Unknown Outlook default folder: {name_or_id}") from exc
        try:
            com = self._namespace.GetDefaultFolder(folder_id)
        except Exception as exc:
            raise LookupError(f"Outlook default folder unavailable: {name_or_id}") from exc
        return OutlookFolder(com, _owner=self)

    def create_mail(self, *, to: str = "", subject: str = "", body: str = "") -> OutlookMail:
        """Create an unsent message; call save() or save_as() to persist it."""
        mail = OutlookMail(self._com.CreateItem(0), _owner=self, _created=True)  # olMailItem
        self._opened.append(mail)
        mail.to, mail.subject, mail.body = to, subject, body
        return mail

    def create_appointment(self, *, subject: str = "") -> OutlookAppointment:
        """Create an unsaved calendar appointment without sending invitations."""
        item = OutlookAppointment(self._com.CreateItem(1), _owner=self, _created=True)
        self._opened.append(item)
        item.subject = subject
        return item

    def open_item(self, path: str | Path) -> OutlookItem:
        """Open a local mail/calendar MSG file; the wrapper owns only this item."""
        try:
            com = self._namespace.OpenSharedItem(str(Path(path).resolve()))
        except Exception as exc:
            raise LookupError(f"Outlook item file unavailable: {path}") from exc
        try:
            item = _wrap_item(com, owner=self)
        except BaseException:
            try:
                com.Close(1)
            except Exception:
                pass
            raise
        self._opened.append(item)
        return item

    def quit(self, *, save_changes: bool = False) -> None:
        """Close owned items; quit/wait for the process only in exclusive mode."""
        failed: list[OutlookItem] = []
        errors: list[str] = []
        for item in self._opened:
            try:
                item.close(save=save_changes)
            except Exception as exc:
                failed.append(item)
                errors.append(str(exc))
        self._opened = failed
        if errors:
            raise RuntimeError("Could not close Outlook items: " + "; ".join(errors))
        if self._session is not None:
            self._session.quit(self._com, release=self._release_com)
        else:
            self._release_com()

    def _release_com(self) -> None:
        for resource in self._resources:
            resource._com = None
        self._resources.clear()
        self._namespace = None
        self._com = None

    def __enter__(self) -> OutlookApp:
        return self

    def __exit__(self, *_: Any) -> None:
        self.quit()
