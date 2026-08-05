"""PyQt5 (Qt 5) reference demo app — Qt 5 twin of ``all_widgets.py``.

Mirrors the PySide6 demo's widget tree exactly (same ``objectName`` and
``accessibleName`` for every control) so the integration tests in
``tests/test_qt5.py`` can exercise the same surface against Qt 5.

PyQt5 chosen over PySide2 because PySide2 has no wheel for Python ≥ 3.12 —
PyQt5 5.15 still ships ``cp38-abi3`` wheels that work on 3.13.

Run::

    python examples/qt_demo/all_widgets_qt5.py

CLI flags identical to the Qt 6 demo: ``--no-show``, ``--quit-after N``.
"""

from __future__ import annotations

import argparse
import sys

from PyQt5.QtCore import QDate, Qt, QTimer
from PyQt5.QtGui import QStandardItem, QStandardItemModel
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSlider,
    QSpinBox,
    QStatusBar,
    QTableView,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

WINDOW_TITLE = "Dolphin Qt5 Demo"
WINDOW_OBJECT_NAME = "qt5_demo_main_window"


def _named(widget: QWidget, object_name: str, accessible_name: str | None = None) -> QWidget:
    """Set both ``objectName`` and ``accessibleName`` on *widget* (Qt 5 path)."""
    widget.setObjectName(object_name)
    widget.setAccessibleName(accessible_name or object_name)
    return widget


class CustomDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Custom Dialog")
        self.setObjectName("qt5_custom_dialog")
        self.setAccessibleName("Custom Dialog")
        self.resize(320, 160)

        layout = QFormLayout(self)

        self.name_input = _named(QLineEdit(self), "qt5_custom_dialog_name", "Name")
        layout.addRow("Name:", self.name_input)

        self.email_input = _named(QLineEdit(self), "qt5_custom_dialog_email", "Email")
        layout.addRow("Email:", self.email_input)

        # Qt 5: StandardButton enum members are flat (Qt 5 supports both flat and
        # scoped access, we use flat for clarity).
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
            self,
        )
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        if ok_btn is not None:
            _named(ok_btn, "qt5_custom_dialog_ok", "OK")
        if cancel_btn is not None:
            _named(cancel_btn, "qt5_custom_dialog_cancel", "Cancel")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)


class DemoMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setAccessibleName(WINDOW_TITLE)
        self.resize(900, 640)

        self.status = QStatusBar(self)
        self.status.setObjectName("qt5_status_bar")
        self.setStatusBar(self.status)
        self.status_label = QLabel("status: ready", self)
        self.status_label.setObjectName("qt5_status_label")
        self.status_label.setAccessibleName("status: ready")
        self.status.addPermanentWidget(self.status_label, 1)

        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_dock()

    # ------------------------------------------------------------------ status

    def _set_status(self, text: str) -> None:
        """Publish *text* to the persistent status label.

        Updates both visible text and accessibleName so UIA Name property
        stays in sync (tests read it that way).
        """
        msg = f"status: {text}"
        self.status_label.setText(msg)
        self.status_label.setAccessibleName(msg)

    # ------------------------------------------------------------------ menu

    def _build_menu(self) -> None:
        menubar = self.menuBar()
        menubar.setObjectName("qt5_menubar")

        file_menu = menubar.addMenu("&File")
        file_menu.setObjectName("qt5_menu_file")
        new_act = QAction("New", self)
        new_act.setObjectName("qt5_action_new")
        new_act.triggered.connect(lambda: self._set_status("menu File>New"))
        open_act = QAction("Open...", self)
        open_act.setObjectName("qt5_action_open")
        open_act.triggered.connect(lambda: self._set_status("menu File>Open"))
        quit_act = QAction("Quit", self)
        quit_act.setObjectName("qt5_action_quit")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(new_act)
        file_menu.addAction(open_act)
        file_menu.addSeparator()
        file_menu.addAction(quit_act)

        edit_menu = menubar.addMenu("&Edit")
        edit_menu.setObjectName("qt5_menu_edit")
        copy_act = QAction("Copy", self)
        copy_act.setObjectName("qt5_action_copy")
        copy_act.triggered.connect(lambda: self._set_status("menu Edit>Copy"))
        paste_act = QAction("Paste", self)
        paste_act.setObjectName("qt5_action_paste")
        paste_act.triggered.connect(lambda: self._set_status("menu Edit>Paste"))
        edit_menu.addAction(copy_act)
        edit_menu.addAction(paste_act)

        view_menu = menubar.addMenu("&View")
        view_menu.setObjectName("qt5_menu_view")
        zoom_menu = view_menu.addMenu("Zoom")
        zoom_menu.setObjectName("qt5_menu_zoom")
        for level in ("50%", "100%", "200%"):
            act = QAction(level, self)
            act.setObjectName(f"qt5_action_zoom_{level.rstrip('%')}")
            act.triggered.connect(
                lambda _checked=False, lv=level: self._set_status(f"menu View>Zoom>{lv}")
            )
            zoom_menu.addAction(act)

    # ------------------------------------------------------------------ toolbar

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main Toolbar", self)
        toolbar.setObjectName("qt5_toolbar_main")
        self.addToolBar(toolbar)

        bold_act = QAction("Bold", self)
        bold_act.setObjectName("qt5_action_bold")
        bold_act.setCheckable(True)
        bold_act.toggled.connect(lambda v: self._set_status(f"toolbar bold={v}"))
        italic_act = QAction("Italic", self)
        italic_act.setObjectName("qt5_action_italic")
        italic_act.setCheckable(True)
        italic_act.toggled.connect(lambda v: self._set_status(f"toolbar italic={v}"))
        toolbar.addAction(bold_act)
        toolbar.addAction(italic_act)

    # ------------------------------------------------------------------ central

    def _build_central(self) -> None:
        tabs = QTabWidget(self)
        tabs.setObjectName("qt5_main_tabs")
        tabs.setAccessibleName("Main Tabs")
        self.setCentralWidget(tabs)

        tabs.addTab(self._build_buttons_tab(), "Buttons")
        tabs.addTab(self._build_inputs_tab(), "Inputs")
        tabs.addTab(self._build_choices_tab(), "Choices")
        tabs.addTab(self._build_containers_tab(), "Containers")
        tabs.addTab(self._build_dialogs_tab(), "Dialogs")

    def _build_buttons_tab(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("qt5_tab_buttons")
        layout = QVBoxLayout(page)

        ok_btn = _named(QPushButton("OK", page), "qt5_btn_ok", "OK")
        ok_btn.clicked.connect(lambda: self._set_status("clicked OK"))
        layout.addWidget(ok_btn)

        cancel_btn = _named(QPushButton("Cancel", page), "qt5_btn_cancel", "Cancel")
        cancel_btn.clicked.connect(lambda: self._set_status("clicked Cancel"))
        layout.addWidget(cancel_btn)

        toggle_btn = _named(QPushButton("Toggle", page), "qt5_btn_toggle", "Toggle")
        toggle_btn.setCheckable(True)
        toggle_btn.toggled.connect(lambda v: self._set_status(f"toggle={v}"))
        layout.addWidget(toggle_btn)

        disabled_btn = _named(QPushButton("Disabled", page), "qt5_btn_disabled", "Disabled")
        disabled_btn.setEnabled(False)
        layout.addWidget(disabled_btn)

        chk_remember = _named(QCheckBox("Remember me", page), "qt5_chk_remember", "Remember me")
        chk_remember.toggled.connect(lambda v: self._set_status(f"remember={v}"))
        layout.addWidget(chk_remember)

        chk_updates = _named(QCheckBox("Send updates", page), "qt5_chk_updates", "Send updates")
        chk_updates.setChecked(True)
        chk_updates.toggled.connect(lambda v: self._set_status(f"updates={v}"))
        layout.addWidget(chk_updates)

        radio_group = QGroupBox("Pick one", page)
        radio_group.setObjectName("qt5_group_pick")
        radio_layout = QVBoxLayout(radio_group)
        for label in ("Option A", "Option B", "Option C"):
            rb = _named(
                QRadioButton(label, radio_group),
                f"qt5_radio_{label.split()[-1].lower()}",
                label,
            )
            rb.toggled.connect(lambda v, lb=label: v and self._set_status(f"selected {lb}"))
            radio_layout.addWidget(rb)
        layout.addWidget(radio_group)

        layout.addStretch(1)
        return page

    def _build_inputs_tab(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("qt5_tab_inputs")
        layout = QFormLayout(page)

        username = _named(QLineEdit(page), "qt5_input_username", "Username")
        username.textEdited.connect(lambda t: self._set_status(f"username={t}"))
        layout.addRow("Username:", username)

        password = _named(QLineEdit(page), "qt5_input_password", "Password")
        password.setEchoMode(QLineEdit.Password)
        layout.addRow("Password:", password)

        readonly = _named(QLineEdit("read-only", page), "qt5_input_readonly", "Readonly")
        readonly.setReadOnly(True)
        layout.addRow("Readonly:", readonly)

        spinner = _named(QSpinBox(page), "qt5_spin_count", "Count")
        spinner.setRange(0, 100)
        spinner.setValue(10)
        spinner.valueChanged.connect(lambda v: self._set_status(f"count={v}"))
        layout.addRow("Count:", spinner)

        double_spinner = _named(QDoubleSpinBox(page), "qt5_spin_price", "Price")
        double_spinner.setRange(0.0, 9999.99)
        double_spinner.setValue(19.99)
        double_spinner.setDecimals(2)
        layout.addRow("Price:", double_spinner)

        slider = _named(QSlider(Qt.Horizontal, page), "qt5_slider_volume", "Volume")
        slider.setRange(0, 100)
        slider.setValue(50)
        slider.valueChanged.connect(lambda v: self._set_status(f"volume={v}"))
        layout.addRow("Volume:", slider)

        date = _named(QDateEdit(page), "qt5_date_birth", "Birth date")
        date.setDate(QDate(2000, 1, 1))
        date.setCalendarPopup(True)
        layout.addRow("Birth date:", date)

        notes = _named(QPlainTextEdit(page), "qt5_input_notes", "Notes")
        notes.setPlaceholderText("Multi-line notes here…")
        layout.addRow("Notes:", notes)

        return page

    def _build_choices_tab(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("qt5_tab_choices")
        layout = QFormLayout(page)

        combo = _named(QComboBox(page), "qt5_combo_language", "Language")
        combo.addItems(["English", "Polish", "German", "Japanese"])
        combo.currentTextChanged.connect(lambda t: self._set_status(f"language={t}"))
        layout.addRow("Language:", combo)

        editable_combo = _named(QComboBox(page), "qt5_combo_city", "City")
        editable_combo.setEditable(True)
        editable_combo.addItems(["Warsaw", "Berlin", "Tokyo"])
        editable_combo.currentTextChanged.connect(lambda t: self._set_status(f"city={t}"))
        layout.addRow("City:", editable_combo)

        listbox = _named(QListWidget(page), "qt5_list_fruits", "Fruits")
        for fruit in ("Apple", "Banana", "Cherry", "Date"):
            listbox.addItem(fruit)
        listbox.currentTextChanged.connect(lambda t: self._set_status(f"fruit={t}"))
        layout.addRow("Fruits:", listbox)

        return page

    def _build_containers_tab(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("qt5_tab_containers")
        layout = QVBoxLayout(page)

        tree = _named(QTreeWidget(page), "qt5_tree_files", "Files")
        tree.setHeaderLabels(["Name", "Type"])
        root = QTreeWidgetItem(["Project", "folder"])
        root.addChild(QTreeWidgetItem(["src", "folder"]))
        src_child = QTreeWidgetItem(["main.py", "file"])
        root.child(0).addChild(src_child)
        root.addChild(QTreeWidgetItem(["README.md", "file"]))
        tree.addTopLevelItem(root)
        tree.expandAll()
        tree.itemClicked.connect(lambda it, _col: self._set_status(f"tree={it.text(0)}"))
        layout.addWidget(QLabel("Tree:", page))
        layout.addWidget(tree)

        table = _named(QTableView(page), "qt5_table_people", "People")
        model = QStandardItemModel(3, 3, self)
        model.setHorizontalHeaderLabels(["Name", "Age", "City"])
        rows = [
            ("Alice", "30", "Warsaw"),
            ("Bob", "42", "Berlin"),
            ("Carol", "25", "Tokyo"),
        ]
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                model.setItem(r, c, QStandardItem(val))
        table.setModel(model)
        layout.addWidget(QLabel("People:", page))
        layout.addWidget(table)

        return page

    def _build_dialogs_tab(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("qt5_tab_dialogs")
        layout = QVBoxLayout(page)

        info_btn = _named(QPushButton("Show info", page), "qt5_btn_show_info", "Show info")
        info_btn.clicked.connect(self._show_info)
        layout.addWidget(info_btn)

        confirm_btn = _named(
            QPushButton("Show confirm", page), "qt5_btn_show_confirm", "Show confirm"
        )
        confirm_btn.clicked.connect(self._show_confirm)
        layout.addWidget(confirm_btn)

        input_btn = _named(QPushButton("Show input", page), "qt5_btn_show_input", "Show input")
        input_btn.clicked.connect(self._show_input)
        layout.addWidget(input_btn)

        custom_btn = _named(QPushButton("Show custom", page), "qt5_btn_show_custom", "Show custom")
        custom_btn.clicked.connect(self._show_custom)
        layout.addWidget(custom_btn)

        layout.addStretch(1)
        return page

    def _build_dock(self) -> None:
        dock = QDockWidget("Properties", self)
        dock.setObjectName("qt5_dock_properties")
        dock_content = QWidget(dock)
        dock_layout = QVBoxLayout(dock_content)
        info = _named(QLabel("Property pane", dock_content), "qt5_dock_label", "Property pane")
        dock_layout.addWidget(info)
        dock_layout.addStretch(1)
        dock.setWidget(dock_content)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

    # ------------------------------------------------------------------ dialogs

    def _show_info(self) -> None:
        box = QMessageBox(self)
        box.setObjectName("qt5_msg_info")
        box.setWindowTitle("Info")
        box.setText("Hello from Qt5 Demo")
        box.setIcon(QMessageBox.Information)
        ok_btn = box.addButton(QMessageBox.Ok)
        _named(ok_btn, "qt5_msg_info_ok", "OK")
        box.exec_()
        self._set_status("info dismissed")

    def _show_confirm(self) -> None:
        box = QMessageBox(self)
        box.setObjectName("qt5_msg_confirm")
        box.setWindowTitle("Confirm")
        box.setText("Are you sure?")
        box.setIcon(QMessageBox.Question)
        yes_btn = box.addButton(QMessageBox.Yes)
        no_btn = box.addButton(QMessageBox.No)
        _named(yes_btn, "qt5_msg_confirm_yes", "Yes")
        _named(no_btn, "qt5_msg_confirm_no", "No")
        box.exec_()
        clicked = box.clickedButton()
        choice = "yes" if clicked is yes_btn else "no"
        self._set_status(f"confirm={choice}")

    def _show_input(self) -> None:
        text, ok = QInputDialog.getText(self, "Input", "Enter value:")
        if ok:
            self._set_status(f"input={text}")
        else:
            self._set_status("input cancelled")

    def _show_custom(self) -> None:
        dlg = CustomDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            name = dlg.name_input.text()
            email = dlg.email_input.text()
            self._set_status(f"custom: {name}/{email}")
        else:
            self._set_status("custom cancelled")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-show", action="store_true", help="Build UI but don't show it.")
    parser.add_argument("--quit-after", type=float, default=0.0, help="Quit N seconds after show.")
    args = parser.parse_args(argv)

    app = QApplication.instance() or QApplication(sys.argv)
    win = DemoMainWindow()
    if not args.no_show:
        win.show()

    if args.quit_after > 0:
        QTimer.singleShot(int(args.quit_after * 1000), app.quit)
    if args.no_show:
        app.processEvents()
        return 0

    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
