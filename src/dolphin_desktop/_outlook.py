"""Classic Outlook COM facade; never terminate the shared Outlook session."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from ._office import _require_win32com

_FOLDERS = {"inbox": 6, "drafts": 16, "sent": 5, "deleted": 3, "calendar": 9}


class OutlookAttachment:
    """A mail attachment that can be saved to an explicit local path."""

    def __init__(self, _com: Any) -> None:
        self._com = _com

    @property
    def name(self) -> str:
        """Original attachment file name."""
        return str(self._com.FileName)

    def save_as(self, path: str | Path) -> None:
        """Save attachment bytes to the caller's chosen path."""
        self._com.SaveAsFile(str(Path(path).resolve()))


class OutlookItem:
    """Common mail/calendar fields and explicit persistence operations."""

    def __init__(self, _com: Any) -> None:
        self._com = _com
        self._closed = False

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
        return OutlookAttachment(self._com.Attachments.Item(index))

    def add_attachment(self, path: str | Path) -> OutlookAttachment:
        """Attach a local file by value."""
        return OutlookAttachment(self._com.Attachments.Add(str(Path(path).resolve()), 1))

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


def _wrap_item(com: Any) -> OutlookItem:
    if com.Class == 43:  # olMail
        return OutlookMail(com)
    if com.Class == 26:  # olAppointment
        return OutlookAppointment(com)
    raise TypeError(f"Unsupported Outlook item class: {com.Class}")


class OutlookFolder:
    """Outlook folder with 1-based, read-only item lookup."""

    def __init__(self, _com: Any) -> None:
        self._com = _com

    @property
    def name(self) -> str:
        """Folder display name."""
        return str(self._com.Name)

    @property
    def item_count(self) -> int:
        """Number of items, including classes outside the mail/calendar API."""
        return int(self._com.Items.Count)

    def item(self, index: int) -> OutlookItem:
        """Return mail/appointment by 1-based index; reject unsupported classes."""
        return _wrap_item(self._com.Items.Item(index))

    def folder(self, name_or_index: str | int) -> OutlookFolder:
        """Return a subfolder by name or 1-based index."""
        return OutlookFolder(self._com.Folders.Item(name_or_index))


class OutlookApp:
    """COM API for classic Outlook with an existing configured MAPI profile.

    No implicit sending, logon, logoff or application Quit occurs. Context exit
    closes only items created/opened here and leaves existing Outlook work alone.
    New Outlook does not provide this COM object model.
    """

    def __init__(self, _com: Any) -> None:
        self._com = _com
        self._opened: list[OutlookItem] = []

    @classmethod
    def connect(cls) -> OutlookApp:
        """Attach to running classic Outlook or raise RuntimeError."""
        try:
            return cls(_require_win32com().GetActiveObject("Outlook.Application"))
        except Exception as exc:
            raise RuntimeError("No running classic Outlook instance found.") from exc

    @classmethod
    def start(cls) -> OutlookApp:
        """Start or reuse classic Outlook; requires a configured MAPI profile."""
        try:
            return cls(_require_win32com().Dispatch("Outlook.Application"))
        except Exception as exc:
            raise RuntimeError(
                "Classic Outlook COM is unavailable; install classic Outlook and "
                "configure a MAPI profile. New Outlook is not supported by COM."
            ) from exc

    def default_folder(self, name_or_id: str | int = "inbox") -> OutlookFolder:
        """Get inbox/drafts/sent/deleted/calendar or an OlDefaultFolders integer."""
        folder_id = _FOLDERS[name_or_id.lower()] if isinstance(name_or_id, str) else name_or_id
        return OutlookFolder(self._com.GetNamespace("MAPI").GetDefaultFolder(folder_id))

    def create_mail(self, *, to: str = "", subject: str = "", body: str = "") -> OutlookMail:
        """Create an unsent message; call save() or save_as() to persist it."""
        mail = OutlookMail(self._com.CreateItem(0))  # olMailItem
        self._opened.append(mail)
        mail.to, mail.subject, mail.body = to, subject, body
        return mail

    def create_appointment(self, *, subject: str = "") -> OutlookAppointment:
        """Create an unsaved calendar appointment without sending invitations."""
        item = OutlookAppointment(self._com.CreateItem(1))  # olAppointmentItem
        self._opened.append(item)
        item.subject = subject
        return item

    def open_item(self, path: str | Path) -> OutlookItem:
        """Open a local mail/calendar MSG file; the wrapper owns only this item."""
        com = self._com.GetNamespace("MAPI").OpenSharedItem(str(Path(path).resolve()))
        try:
            item = _wrap_item(com)
        except BaseException:
            try:
                com.Close(1)
            except Exception:
                pass
            raise
        self._opened.append(item)
        return item

    def quit(self, *, save_changes: bool = False) -> None:
        """Close owned items, retaining failures for retry; never quit Outlook."""
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

    def __enter__(self) -> OutlookApp:
        return self

    def __exit__(self, *_: Any) -> None:
        self.quit()
