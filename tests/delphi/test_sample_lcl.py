"""Controlled LCL locator/action regressions; full application acceptance lives in .ta."""


def test_button_click_save_updates_status(lcl_app):
    _, form = lcl_app
    form.component(cls="TEdit", near_label="Name:").set_text("Ada")
    form.component(cls="TEdit", near_label="Age:").set_text("40")
    form.component(cls="TButton", title="Save").click()
    status = form.component(cls="TLabel", title_re=r"^Saved:")
    status.wait_for_text("name=Ada", timeout=3)
    assert "age=40" in status.text()


def test_button_click_clear_resets(lcl_app):
    _, form = lcl_app
    form.component(cls="TEdit", near_label="Name:").set_text("Wipe me")
    form.component(cls="TButton", title="Clear").click()
    name = form.component(cls="TEdit", near_label="Name:")
    name.wait_for_text("", contains=False, timeout=3)
    # BtnClearClick blanks EdtName and writes 'Cleared.' into LblStatus.
    assert name.text() == ""
    assert form.component(cls="TLabel", title_re=r"^Cleared\.").text() == "Cleared."


def test_checkbox_check_idempotent(lcl_app):
    _, form = lcl_app
    chk = form.component(cls="TCheckBox", title="Active")
    chk.check()
    chk.check()
    chk.wait_for_checked(checked=True, timeout=3)
    assert chk.is_checked() is True
