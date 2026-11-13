"""Real VCL support boundary, independent of the Lazarus suite / DESKTOP-161."""

import pytest

from dolphin_desktop import ElementNotFoundError

pytestmark = [pytest.mark.component, pytest.mark.windows_component]


def test_stock_vcl_does_not_publish_component_names(real_vcl_app, record_property):
    """Genuine Delphi Names do not become AutomationIds by compiler version."""
    app = real_vcl_app
    form = app.form(title="Dolphin real VCL reference", timeout=10)
    form.wait_ready(timeout=5)
    record_property("form_automation_id", form.pywinauto.element_info.automation_id)
    assert form.pywinauto.element_info.automation_id == ""
    children = form.components()
    record_property("uia_components", repr([(c.cls, c.name, c.text()) for c in children]))
    for cls, name in [("TEdit", "EdtName"), ("TButton", "BtnSave")]:
        controls = form.components(cls=cls)
        assert len(controls) == 1
        assert controls[0].pywinauto.element_info.automation_id.isdecimal()
        with pytest.raises(ElementNotFoundError, match=r"TComponent\.Name is not automatically"):
            form.component(name=name, cls=cls, timeout=0.2)
    with pytest.raises(ElementNotFoundError, match=r"TForm\.Name is not automatically"):
        app.form(name="MainForm", timeout=0.2)
    assert not form.components(cls="TLabel")


def test_stock_vcl_explicit_uia_edit_save_reference(real_vcl_app):
    """Supported UIA workflow; this does not satisfy lookup by Delphi Name."""
    form = real_vcl_app.form(title="Dolphin real VCL reference", timeout=10)
    edit = form.component(cls="TEdit", index=0)
    edit.set_text("Alice")
    edit.wait_for_text("Alice", timeout=3)
    form.component(cls="TButton", title="Save").click()
    edit.wait_for_text("Saved: Alice", timeout=3)
