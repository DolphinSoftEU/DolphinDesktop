"""Office COM wrappers must never touch documents the operator opened."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from dolphin_desktop import _office
from dolphin_desktop._office import (
    ExcelApp,
    ExcelWorkbook,
    WordApp,
    WordDocument,
)


class _FakeDoc:
    """Workbook / document stand-in recording what was done to it."""

    def __init__(self, name: str, app: _FakeApp | None = None) -> None:
        self.Name = name
        self._app = app
        self.close_arg: object = "not closed"
        self.saves = 0
        #: Exception Save() raises — a read-only or password-protected file.
        self.save_error: Exception | None = None
        #: DisplayAlerts as it stood when Save() ran.
        self.alerts_during_save: object = None

    def Save(self) -> None:  # noqa: N802
        self.alerts_during_save = self._app.DisplayAlerts if self._app else None
        if self.save_error is not None:
            raise self.save_error
        self.saves += 1

    def Close(self, save_changes) -> None:  # noqa: N802
        self.close_arg = save_changes


class _FakeCollection:
    def __init__(self, app: _FakeApp) -> None:
        self._app = app
        #: Exception Open() raises — missing, locked or corrupt file.
        self.open_error: Exception | None = None

    def Open(self, path: str, *args) -> _FakeDoc:  # noqa: N802
        self.security_during_open = self._app.AutomationSecurity
        self.update_links_during_open = self._app.Options.UpdateLinksAtOpen
        self.last_open_args = args
        self._app.events.append(("open", self.security_during_open))
        if self.open_error is not None:
            raise self.open_error
        # Harmless synthetic auto-open macro: an in-memory counter only.
        if self.security_during_open == 1 and not self._app.policy_blocks_macros:
            self._app.macro_runs += 1
        doc = _FakeDoc(path, self._app)
        self._app.documents.append(doc)
        return doc

    def __iter__(self):
        return iter(self._app.documents)


class _FakeOptions:
    """Word options recording link updates and rejecting simulated policy changes."""

    def __init__(self) -> None:
        self._update_links = True
        self.rejected_value: bool | None = None
        self.changes: list[bool] = []

    @property
    def UpdateLinksAtOpen(self) -> bool:  # noqa: N802
        return self._update_links

    @UpdateLinksAtOpen.setter
    def UpdateLinksAtOpen(self, value: bool) -> None:  # noqa: N802
        self.changes.append(value)
        if value is self.rejected_value:
            raise RuntimeError("link setting rejected")
        self._update_links = value


class _FakeApp:
    """Excel.Application / Word.Application stand-in."""

    def __init__(self) -> None:
        self.events: list[tuple[str, int]] = []
        self._security = 2
        self.security_error: int | None = None
        self.macro_runs = 0
        self.policy_blocks_macros = False
        self.Options = _FakeOptions()
        self.Visible = False
        self.DisplayAlerts = True
        self.quit_calls = 0
        self.documents: list[_FakeDoc] = []
        self.Workbooks = _FakeCollection(self)
        self.Documents = self.Workbooks

    @property
    def AutomationSecurity(self) -> int:  # noqa: N802
        return self._security

    @AutomationSecurity.setter
    def AutomationSecurity(self, value: int) -> None:  # noqa: N802
        self.events.append(("security", value))
        if value == self.security_error:
            raise RuntimeError("security setting rejected")
        self._security = value

    def Quit(self) -> None:  # noqa: N802
        self.events.append(("quit", self.AutomationSecurity))
        self.quit_calls += 1

    def preexisting(self, name: str) -> _FakeDoc:
        """Add a document the operator already had open in this instance."""
        doc = _FakeDoc(name, self)
        self.documents.append(doc)
        return doc


def _client(monkeypatch) -> _FakeApp:
    app = _FakeApp()
    client = MagicMock()
    client.DispatchEx.return_value = app
    client.GetActiveObject.return_value = app
    monkeypatch.setattr(_office, "_require_win32com", lambda: client)
    app.client = client  # type: ignore[attr-defined]
    return app


# open() must not hijack a running instance


def test_excel_open_creates_a_private_instance(monkeypatch) -> None:
    """A plain Dispatch binds to the operator's Excel through the ROT."""
    app = _client(monkeypatch)
    ExcelApp.open("book.xlsx")
    app.client.Dispatch.assert_not_called()
    app.client.DispatchEx.assert_called_once_with("Excel.Application")


def test_word_open_creates_a_private_instance(monkeypatch) -> None:
    app = _client(monkeypatch)
    WordApp.open("letter.docx")
    app.client.Dispatch.assert_not_called()
    app.client.DispatchEx.assert_called_once_with("Word.Application")


# KAN-574 — open() must force-disable macros and not auto-update external links


def test_excel_open_force_disables_macros_and_link_updates(monkeypatch) -> None:
    app = _client(monkeypatch)
    ExcelApp.open("book.xlsx")
    assert app.Workbooks.security_during_open == 3
    assert app.AutomationSecurity == 2
    assert app.AskToUpdateLinks is False
    assert app.Workbooks.last_open_args == (0,)  # UpdateLinks=0
    assert app.macro_runs == 0


def test_word_open_force_disables_macros(monkeypatch) -> None:
    app = _client(monkeypatch)
    WordApp.open("letter.docx")
    assert app.Documents.security_during_open == 3
    assert app.AutomationSecurity == 2
    assert app.macro_runs == 0


@pytest.mark.parametrize("previous_links", [False, True])
@pytest.mark.parametrize("allow_macros", [False, True])
@pytest.mark.parametrize("open_fails", [False, True])
def test_word_disables_link_updates_during_open_and_restores_options(
    monkeypatch, previous_links, allow_macros, open_fails
):
    app = _client(monkeypatch)
    app.Options._update_links = previous_links
    if open_fails:
        app.Documents.open_error = RuntimeError("Open failed")
        with pytest.raises(RuntimeError, match="Open failed"):
            WordApp.open("linked.docx", allow_macros=allow_macros)
        assert app.quit_calls == 1
    else:
        WordApp.open("linked.docx", allow_macros=allow_macros)
    assert app.Documents.update_links_during_open is False
    assert app.Options.UpdateLinksAtOpen is previous_links
    assert app.Options.changes == [False, previous_links]
    assert app.AutomationSecurity == 2


@pytest.mark.parametrize("rejected_links", [False, True])
def test_word_link_setting_failure_fails_closed_and_quits(monkeypatch, rejected_links):
    app = _client(monkeypatch)
    app.Options.rejected_value = rejected_links
    with pytest.raises(RuntimeError, match="link setting rejected"):
        WordApp.open("linked.docx")
    assert app.AutomationSecurity == 2
    assert app.quit_calls == 1
    assert app.macro_runs == 0
    if rejected_links is False:
        assert not app.documents
    else:
        assert app.documents[0].close_arg == 0


def test_word_restores_link_options_even_if_security_restoration_fails(monkeypatch):
    app = _client(monkeypatch)
    app.security_error = 2
    with pytest.raises(RuntimeError, match="security setting rejected"):
        WordApp.open("linked.docx")
    assert app.Options.UpdateLinksAtOpen is True
    assert app.Options.changes == [False, True]
    assert app.quit_calls == 1


@pytest.mark.parametrize("wrapper", [ExcelApp, WordApp])
@pytest.mark.parametrize("previous_security", [1, 2, 3])
@pytest.mark.parametrize("allow_macros", [False, True])
def test_open_scopes_macro_security_to_the_open_call(
    monkeypatch, wrapper, previous_security, allow_macros
) -> None:
    app = _client(monkeypatch)
    app._security = previous_security
    security = 1 if allow_macros else 3

    wrapper.open("synthetic-macro-document", allow_macros=allow_macros, visible=False)

    assert app.events == [
        ("security", security),
        ("open", security),
        ("security", previous_security),
    ]
    assert app.macro_runs == int(allow_macros)
    assert app.AutomationSecurity == previous_security
    assert app.Visible is False
    if wrapper is ExcelApp:
        assert app.Workbooks.last_open_args == (0,)  # Also with macros enabled.
        assert app.AskToUpdateLinks is False


@pytest.mark.parametrize("wrapper", [ExcelApp, WordApp])
@pytest.mark.parametrize("allow_macros", [False, True])
def test_open_restores_security_before_quitting_after_error(monkeypatch, wrapper, allow_macros):
    app = _client(monkeypatch)
    error = RuntimeError("Open failed")
    app.Workbooks.open_error = error
    with pytest.raises(RuntimeError, match="Open failed") as caught:
        wrapper.open("synthetic-macro-document", allow_macros=allow_macros)
    assert caught.value is error
    assert app.events[-2:] == [("security", 2), ("quit", 2)]
    assert app.quit_calls == 1


@pytest.mark.parametrize("wrapper", [ExcelApp, WordApp])
@pytest.mark.parametrize("rejected_security", [3, 2])
def test_security_setting_failure_closes_private_instance(monkeypatch, wrapper, rejected_security):
    app = _client(monkeypatch)
    app.security_error = rejected_security
    with pytest.raises(RuntimeError, match="security setting rejected"):
        wrapper.open("synthetic-macro-document")
    assert app.macro_runs == 0
    assert app.quit_calls == 1
    if rejected_security == 3:
        assert not app.documents  # Fail closed: Open was never reached.
    else:
        assert app.documents[0].close_arg == 0  # Open succeeded, restoration failed.


@pytest.mark.parametrize("wrapper", [ExcelApp, WordApp])
def test_macro_opt_in_does_not_guarantee_execution_under_office_policy(monkeypatch, wrapper):
    app = _client(monkeypatch)
    app.policy_blocks_macros = True
    wrapper.open("synthetic-macro-document", allow_macros=True)
    assert app.Workbooks.security_during_open == 1
    assert app.macro_runs == 0
    assert app.AutomationSecurity == 2


def test_xlm_security_prompt_is_not_suppressed_by_force_disable(monkeypatch):
    app = _client(monkeypatch)
    app.Workbooks.open_error = RuntimeError("Excel 4.0/XLM security prompt")
    with pytest.raises(RuntimeError, match="XLM security prompt"):
        ExcelApp.open("synthetic-xlm-workbook.xls")
    assert app.Workbooks.security_during_open == 3
    assert app.events[-2:] == [("security", 2), ("quit", 2)]


@pytest.mark.parametrize("wrapper", [ExcelApp, WordApp])
@pytest.mark.parametrize("invalid", ["false", "true", 0, 1, None])
def test_macro_opt_in_requires_a_boolean_before_com_is_created(monkeypatch, wrapper, invalid):
    app = _client(monkeypatch)
    with pytest.raises(TypeError, match="allow_macros must be a bool"):
        wrapper.open("synthetic-macro-document", allow_macros=invalid)
    app.client.DispatchEx.assert_not_called()


# A failed open() must not strand the private instance


def test_excel_open_quits_its_instance_when_the_workbook_will_not_open(
    monkeypatch,
) -> None:
    """Nothing outside open() holds the DispatchEx handle, and a visible
    Excel with no workbook never self-reaps — one excel.exe per call."""
    app = _client(monkeypatch)
    app.Workbooks.open_error = RuntimeError("file is locked for editing")
    with pytest.raises(RuntimeError, match="locked"):
        ExcelApp.open("book.xlsx")
    assert app.quit_calls == 1


def test_word_open_quits_its_instance_when_the_document_will_not_open(
    monkeypatch,
) -> None:
    app = _client(monkeypatch)
    app.Documents.open_error = RuntimeError("file is corrupt")
    with pytest.raises(RuntimeError, match="corrupt"):
        WordApp.open("letter.docx")
    assert app.quit_calls == 1


# quit() must only close what the wrapper opened


def test_excel_quit_leaves_the_operators_workbooks_alone(monkeypatch) -> None:
    app = _client(monkeypatch)
    budget = app.preexisting("unsaved-budget.xlsx")
    with ExcelApp.open("book.xlsx"):
        pass
    assert budget.close_arg == "not closed"
    assert budget.saves == 0
    assert app.documents[-1].close_arg is False
    assert app.quit_calls == 1


def test_word_quit_leaves_the_operators_documents_alone(monkeypatch) -> None:
    app = _client(monkeypatch)
    draft = app.preexisting("unsaved-draft.docx")
    with WordApp.open("letter.docx"):
        pass
    assert draft.close_arg == "not closed"
    assert draft.saves == 0
    assert app.quit_calls == 1


def test_quit_with_save_changes_saves_only_the_opened_workbook(monkeypatch) -> None:
    app = _client(monkeypatch)
    budget = app.preexisting("unsaved-budget.xlsx")
    ExcelApp.open("book.xlsx").quit(save_changes=True)
    assert budget.saves == 0
    assert app.documents[-1].saves == 1


def test_quit_reports_a_workbook_it_could_not_save(monkeypatch) -> None:
    """Swallowing the Save() failure closed the workbook anyway, so
    quit(save_changes=True) discarded the edits without a word.

    Reporting is only half of it: the workbook stays open and Excel keeps
    running, because closing either one destroys exactly the content the
    failed save was meant to keep. The operator can still recover it by
    hand, and a second quit() retries the save.
    """
    app = _client(monkeypatch)
    xl = ExcelApp.open("book.xlsx")
    doc = app.documents[-1]
    doc.save_error = RuntimeError("read-only recommended")
    with pytest.raises(RuntimeError, match=r"could not save.*book\.xlsx"):
        xl.quit(save_changes=True)
    assert doc.close_arg == "not closed"
    assert app.quit_calls == 0

    doc.save_error = None
    xl.quit(save_changes=True)
    assert doc.saves == 1
    assert app.quit_calls == 1


def test_word_quit_reports_a_document_it_could_not_save(monkeypatch) -> None:
    app = _client(monkeypatch)
    wd = WordApp.open("letter.docx")
    doc = app.documents[-1]
    doc.save_error = RuntimeError("read-only recommended")
    with pytest.raises(RuntimeError, match=r"could not save.*letter\.docx"):
        wd.quit(save_changes=True)
    assert doc.close_arg == "not closed"
    assert app.quit_calls == 0

    doc.save_error = None
    wd.quit(save_changes=True)
    assert doc.saves == 1
    assert app.quit_calls == 1


def test_quit_suppresses_alerts_before_saving_not_after(monkeypatch) -> None:
    """Save() on a never-saved or read-only file raises a modal Save-As
    dialog; with DisplayAlerts still on it blocks the run until a human
    clicks it."""
    app = _client(monkeypatch)
    ExcelApp.open("book.xlsx").quit(save_changes=True)
    assert app.documents[-1].alerts_during_save is False


def test_word_quit_suppresses_alerts_before_saving_not_after(monkeypatch) -> None:
    app = _client(monkeypatch)
    WordApp.open("letter.docx").quit(save_changes=True)
    assert app.documents[-1].alerts_during_save is False


def test_connected_excel_is_never_quit(monkeypatch) -> None:
    """connect() attaches to the operator's own instance — quitting it with
    DisplayAlerts suppressed discards every unsaved edit they had open."""
    app = _client(monkeypatch)
    app.preexisting("unsaved-budget.xlsx")
    ExcelApp.connect().quit()
    assert app.quit_calls == 0
    assert app.DisplayAlerts is True
    assert app.documents[0].close_arg == "not closed"


def test_connected_word_is_never_quit(monkeypatch) -> None:
    app = _client(monkeypatch)
    app.preexisting("unsaved-draft.docx")
    WordApp.connect().quit()
    assert app.quit_calls == 0
    assert app.DisplayAlerts is True


# Late-bound Dispatch takes positional arguments only


def test_workbook_close_passes_save_positionally() -> None:
    com = MagicMock()
    ExcelWorkbook(com).close(save=True)
    com.Close.assert_called_once_with(True)


def test_document_close_passes_a_wdsaveoptions_value_positionally() -> None:
    com = MagicMock()
    WordDocument(com).close(save=True)
    com.Close.assert_called_once_with(-1)
    com.reset_mock()
    WordDocument(com).close()
    com.Close.assert_called_once_with(0)


# Relative save paths belong to the process cwd, not to Office's CurDir


def test_workbook_save_as_resolves_a_relative_path(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    com = MagicMock()
    ExcelWorkbook(com).save_as("report.xlsx")
    (arg,) = com.SaveAs.call_args.args
    assert Path(arg).is_absolute()
    assert Path(arg).parent == tmp_path.resolve()
    assert Path(arg).name == "report.xlsx"


def test_document_save_as_resolves_a_relative_path(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    com = MagicMock()
    WordDocument(com).save_as("letter.docx")
    (arg,) = com.SaveAs2.call_args.args
    assert Path(arg).is_absolute()
    assert Path(arg).parent == tmp_path.resolve()
