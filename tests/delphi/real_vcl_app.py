"""Stock Embarcadero DelphiVCL reference; no accessibility ID injection."""

import os
import sys
from pathlib import Path

from delphivcl import Application, Button, Edit, Form, Label


def main() -> None:
    """Run an owned VCL form with genuine component Names."""
    Application.Initialize()
    form = Form(Application)
    form.SetProps(Name="MainForm", Caption="Dolphin real VCL reference")
    form.SetBounds(100, 100, 480, 240)
    edit = Edit(form)
    edit.SetProps(Parent=form, Name="EdtName", Text="")
    edit.SetBounds(20, 20, 250, 24)
    status = Label(form)
    status.SetProps(Parent=form, Name="LblStatus", Caption="Ready")
    status.SetBounds(20, 100, 300, 24)
    save = Button(form)
    save.SetProps(Parent=form, Name="BtnSave", Caption="Save")
    save.SetBounds(20, 60, 100, 28)

    def on_save(sender: object) -> None:
        status.Caption = "Saved: " + edit.Text
        edit.Text = status.Caption

    save.OnClick = on_save
    form.Show()
    if len(sys.argv) > 1:
        # Windows venv python.exe can redirect to a child interpreter.
        Path(sys.argv[1]).write_text(str(os.getpid()), encoding="ascii")
    Application.Run()
    form.Destroy()


if __name__ == "__main__":
    main()
