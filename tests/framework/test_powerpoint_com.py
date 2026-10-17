"""PowerPoint COM contract and shared-session ownership regressions."""

from unittest.mock import MagicMock, PropertyMock

import pytest

import dolphin_desktop
from dolphin_desktop import PowerPointApp, PowerPointPresentation
from dolphin_desktop import _powerpoint as ppt


@pytest.fixture
def client(monkeypatch):
    client = MagicMock()
    client.Dispatch.return_value.Presentations.Count = 0
    monkeypatch.setattr(ppt, "_require_win32com", lambda: client)
    return client


def test_public_powerpoint_facade_is_exported():
    assert "PowerPointApp" in dolphin_desktop.__all__
    assert dolphin_desktop.PowerPointApp is PowerPointApp


def test_create_and_close_only_owned_presentation(client):
    com = client.Dispatch.return_value
    with PowerPointApp.create(visible=False) as app:
        assert not app.owns_process
        assert app.process_id is None
        assert app.active_presentation._com is com.Presentations.Add.return_value
        com.Presentations.Add.assert_called_once_with(0)
    app.quit()
    com.Presentations.Add.return_value.Close.assert_called_once_with()
    com.Quit.assert_not_called()


def test_connected_presentations_are_never_closed(client):
    com = client.GetActiveObject.return_value
    with PowerPointApp.connect() as app:
        document = app.active_presentation
        assert document._com is com.ActivePresentation
        slide = document.slide(1)
        shape = slide.shape("caption")
    com.ActivePresentation.Close.assert_not_called()
    assert document._com is None
    assert slide._com is None
    assert shape._com is None
    com.Quit.assert_not_called()
    client.GetActiveObject.assert_called_once_with("PowerPoint.Application")


def test_connect_failure_is_clear(client):
    client.GetActiveObject.side_effect = OSError("not running")
    with pytest.raises(RuntimeError, match="No running PowerPoint"):
        PowerPointApp.connect()


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_failed_presentation_creation_preserves_original_error_and_cleans_session(
    client,
    monkeypatch,
    cleanup_fails,
):
    com = client.Dispatch.return_value
    error = OSError("presentation creation failed")
    com.Presentations.Add.side_effect = error
    session = MagicMock(_quit_requested=False)
    if cleanup_fails:
        session.quit.side_effect = OSError("shutdown failed")
    monkeypatch.setattr(ppt, "_start_office", lambda *_, **__: (com, session))
    with pytest.raises(OSError, match="presentation creation failed") as caught:
        PowerPointApp.create(exclusive=True)
    assert caught.value is error
    session.quit.assert_called_once()
    if cleanup_fails:
        assert error.__notes__ == ["Office cleanup also failed: shutdown failed"]


@pytest.mark.parametrize("allow_macros", [False, True])
@pytest.mark.parametrize("open_fails", [False, True])
def test_open_sets_and_restores_security(client, tmp_path, allow_macros, open_fails):
    com = client.Dispatch.return_value
    com.AutomationSecurity = 2
    observed = []

    def open_presentation(*args):
        observed.append((com.AutomationSecurity, args))
        if open_fails:
            raise OSError("corrupt presentation")
        return MagicMock()

    com.Presentations.Open.side_effect = open_presentation
    path = tmp_path / "slides.pptx"
    if open_fails:
        with pytest.raises(OSError, match="corrupt"):
            PowerPointApp.open(path, visible=False, allow_macros=allow_macros)
    else:
        with PowerPointApp.open(path, visible=False, allow_macros=allow_macros):
            pass
    assert observed == [(1 if allow_macros else 3, (str(path.resolve()), 0, 0, 0))]
    assert com.AutomationSecurity == 2
    com.Quit.assert_not_called()


def test_open_rejects_non_bool_before_com_access(client):
    with pytest.raises(TypeError, match="bool"):
        PowerPointApp.open("slides.pptx", allow_macros=1)
    client.Dispatch.assert_not_called()


def test_open_rejects_operator_presentation(client, tmp_path):
    com = client.Dispatch.return_value
    path = tmp_path / "operator.pptx"
    com.Presentations.Count = 1
    existing = com.Presentations.Item.return_value
    existing.FullName = str(path)
    with pytest.raises(RuntimeError, match="already open"):
        PowerPointApp.open(path)
    com.Presentations.Open.assert_not_called()
    existing.Close.assert_not_called()
    com.Quit.assert_not_called()


def test_document_slide_shape_and_persistence_contract(client, tmp_path):
    with PowerPointApp.create() as app:
        document = app.active_presentation
        com = document._com
        com.Name = "slides.pptx"
        com.Slides.Count = 2
        assert document.name == "slides.pptx"
        assert document.slide_count == 2
        slide = document.add_slide()
        com.Slides.Add.assert_called_once_with(3, 12)
        slide._com.SlideIndex = 3
        slide._com.Shapes.Count = 1
        assert slide.index == 3
        assert slide.shape_count == 1
        shape = slide.add_textbox("hello", left=10, top=20, width=300, height=40)
        slide._com.Shapes.AddTextbox.assert_called_once_with(1, 10, 20, 300, 40)
        shape._com.Name = "caption"
        assert shape.name == "caption"
        assert shape.text == "hello"
        shape.text = "updated"
        assert shape.text == "updated"
        slide.shape("caption")
        slide._com.Shapes.Item.assert_called_once_with("caption")
        document.slide(1)
        com.Slides.Item.assert_called_once_with(1)
        document.save_as(tmp_path / "deck.pptx")
        com.SaveAs.assert_called_once_with(str((tmp_path / "deck.pptx").resolve()))
        document.export_pdf(tmp_path / "deck.pdf")
        com.ExportAsFixedFormat.assert_called_once_with(
            str((tmp_path / "deck.pdf").resolve()),
            2,
            1,
            0,
            1,
            1,
            0,
            None,
            1,
        )
        document.save()
        com.Save.assert_called_once_with()
        slide.delete()
        slide._com.Delete.assert_called_once_with()


def test_shape_without_text_frame_is_rejected():
    shape = ppt.PowerPointShape(MagicMock(HasTextFrame=0, Name="picture"))
    with pytest.raises(ValueError, match="no text frame"):
        _ = shape.text
    with pytest.raises(ValueError, match="no text frame"):
        shape.text = "hello"


@pytest.mark.parametrize("operation", ["Save", "Close"])
def test_failed_cleanup_retains_presentation_for_retry(client, operation):
    app = PowerPointApp.create()
    document = app.active_presentation
    getattr(document._com, operation).side_effect = OSError("locked")
    with pytest.raises(RuntimeError, match="locked"):
        app.quit(save_changes=True)
    assert app._opened == [document]
    if operation == "Save":
        document._com.Close.assert_not_called()
    getattr(document._com, operation).side_effect = None
    app.quit(save_changes=True)
    app.quit()
    assert not app._opened
    client.Dispatch.return_value.Quit.assert_not_called()


def test_security_restoration_failure_closes_only_new_document(client, tmp_path):
    com = client.Dispatch.return_value
    type(com).AutomationSecurity = PropertyMock(
        side_effect=[2, None, OSError("restore failed")],
    )
    with pytest.raises(OSError, match="restore failed"):
        PowerPointApp.open(tmp_path / "slides.pptx")
    com.Presentations.Open.return_value.Close.assert_called_once_with()
    com.Quit.assert_not_called()


def test_failed_security_restore_preserves_original_error_if_close_also_fails(client, tmp_path):
    com = client.Dispatch.return_value
    type(com).AutomationSecurity = PropertyMock(side_effect=[2, None, OSError("restore failed")])
    com.Presentations.Open.return_value.Close.side_effect = OSError("close failed")
    with pytest.raises(OSError, match="restore failed"):
        PowerPointApp.open(tmp_path / "slides.pptx")


def test_presentation_close_is_idempotent():
    com = MagicMock()
    doc = PowerPointPresentation(com)
    doc.close(save=True)
    doc.close(save=True)
    com.Save.assert_called_once_with()
    com.Close.assert_called_once_with()
