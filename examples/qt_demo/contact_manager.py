"""Contact Manager demo — CRUD + search + validation surface for E2E tests.

Realistic small business app: left panel = contact list, right panel = form,
top = search bar, bottom = status label that mirrors every action.

Every interactive widget has a deterministic ``objectName`` so tests can
locate it by name. The status label ``cm_status_label`` reflects every
mutation as plain text — tests assert on that string.

Run:: python examples/qt_demo/contact_manager.py
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

WINDOW_TITLE = "Dolphin Contact Manager"
WINDOW_OBJECT_NAME = "cm_main_window"


def _named(widget, object_name, accessible_name=None):
    widget.setObjectName(object_name)
    widget.setAccessibleName(accessible_name or object_name)
    return widget


# Seed records the test suite expects to find at startup.
SEED_CONTACTS = [
    ("Alice", "Cooper", "alice@example.com", "+48 600 100 001", "Friend", "Met at uni 2018", True),
    ("Bob", "Ross", "bob@example.com", "+48 600 100 002", "Work", "Painter on team B", False),
    ("Carol", "Danvers", "carol@example.com", "+48 600 100 003", "Family", "Sister-in-law", True),
    (
        "David",
        "Bowie",
        "david@example.com",
        "+48 600 100 004",
        "Friend",
        "Roommate 2020-2022",
        False,
    ),
    ("Eve", "Polastri", "eve@example.com", "+48 600 100 005", "Work", "Project lead — Q3", True),
]

CATEGORIES = ["Friend", "Family", "Work", "Other"]


class ContactManager(QMainWindow):
    """Single-window contact manager with CRUD + search."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        _named(self, WINDOW_OBJECT_NAME, WINDOW_TITLE)
        self.resize(900, 600)

        # ----- Data store (records by id) -----
        self._next_id = 0
        self._contacts: dict[int, dict] = {}

        # ----- Status bar -----
        bar = QStatusBar(self)
        bar.setObjectName("cm_status_bar")
        self.setStatusBar(bar)
        self.status_label = QLabel("status: ready", self)
        _named(self.status_label, "cm_status_label", "status: ready")
        bar.addPermanentWidget(self.status_label, 1)

        # ----- Central UI -----
        central = QWidget(self)
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # Search bar row
        search_row = QHBoxLayout()
        self.search_edit = _named(QLineEdit(self), "cm_search", "Search")
        self.search_edit.setPlaceholderText("Search by name or email…")
        self.search_edit.textChanged.connect(self._on_search_changed)
        search_row.addWidget(QLabel("Search:", self))
        search_row.addWidget(self.search_edit, 1)

        self.btn_clear_search = _named(
            QPushButton("Clear", self), "cm_btn_clear_search", "Clear search"
        )
        self.btn_clear_search.clicked.connect(self._on_clear_search)
        search_row.addWidget(self.btn_clear_search)

        self.btn_add = _named(QPushButton("+ Add", self), "cm_btn_add", "Add contact")
        self.btn_add.clicked.connect(self._on_add)
        search_row.addWidget(self.btn_add)

        root.addLayout(search_row)

        # Main split (list left, form right) using a simple horizontal layout
        main_row = QHBoxLayout()
        root.addLayout(main_row, 1)

        # Left — contact list
        self.contact_list = _named(QListWidget(self), "cm_contact_list", "Contacts")
        self.contact_list.itemSelectionChanged.connect(self._on_selection_changed)
        main_row.addWidget(self.contact_list, 1)

        # Right — form
        form_widget = QWidget(self)
        form_widget.setObjectName("cm_form_panel")
        form_layout = QFormLayout(form_widget)

        self.first_name = _named(QLineEdit(self), "cm_first_name", "First name")
        form_layout.addRow("First name:", self.first_name)

        self.last_name = _named(QLineEdit(self), "cm_last_name", "Last name")
        form_layout.addRow("Last name:", self.last_name)

        self.email = _named(QLineEdit(self), "cm_email", "Email")
        form_layout.addRow("Email:", self.email)

        # Validation indicator label — reflects @ presence in email
        self.email_status = _named(QLabel("", self), "cm_email_status", "")
        form_layout.addRow("", self.email_status)
        self.email.textChanged.connect(self._on_email_changed)

        self.phone = _named(QLineEdit(self), "cm_phone", "Phone")
        form_layout.addRow("Phone:", self.phone)

        self.category = _named(QComboBox(self), "cm_category", "Category")
        self.category.addItems(CATEGORIES)
        form_layout.addRow("Category:", self.category)

        self.notes = _named(QPlainTextEdit(self), "cm_notes", "Notes")
        self.notes.setPlaceholderText("Free-form notes about this contact…")
        form_layout.addRow("Notes:", self.notes)

        self.favorite = _named(QCheckBox("Favorite", self), "cm_favorite", "Favorite")
        form_layout.addRow("", self.favorite)

        # Action buttons
        btn_row = QHBoxLayout()
        self.btn_save = _named(QPushButton("Save", self), "cm_btn_save", "Save")
        self.btn_save.clicked.connect(self._on_save)
        btn_row.addWidget(self.btn_save)
        self.btn_cancel = _named(QPushButton("Cancel", self), "cm_btn_cancel", "Cancel")
        self.btn_cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(self.btn_cancel)
        self.btn_delete = _named(QPushButton("Delete", self), "cm_btn_delete", "Delete")
        self.btn_delete.clicked.connect(self._on_delete)
        btn_row.addWidget(self.btn_delete)
        btn_row.addStretch(1)
        form_layout.addRow("", self._wrap_layout(btn_row))

        main_row.addWidget(form_widget, 2)

        # ----- Menu -----
        menubar = self.menuBar()
        menubar.setObjectName("cm_menubar")
        file_menu = menubar.addMenu("&File")
        file_menu.setObjectName("cm_menu_file")
        export_act = QAction("Export…", self)
        export_act.setObjectName("cm_action_export")
        export_act.triggered.connect(lambda: self._set_status("menu File>Export"))
        quit_act = QAction("Quit", self)
        quit_act.setObjectName("cm_action_quit")
        quit_act.triggered.connect(self.close)
        file_menu.addAction(export_act)
        file_menu.addSeparator()
        file_menu.addAction(quit_act)

        edit_menu = menubar.addMenu("&Edit")
        edit_menu.setObjectName("cm_menu_edit")
        new_act = QAction("New Contact", self)
        new_act.setObjectName("cm_action_new")
        new_act.triggered.connect(self._on_add)
        edit_menu.addAction(new_act)

        # Populate seed contacts.
        for first, last, email, phone, cat, notes, fav in SEED_CONTACTS:
            self._create_contact(
                first=first,
                last=last,
                email=email,
                phone=phone,
                category=cat,
                notes=notes,
                favorite=fav,
            )

        # Initially clear form (no selection).
        self._clear_form()
        self._set_status("ready")

    # -------------------------------------------------------------- helpers

    def _wrap_layout(self, layout):
        w = QWidget(self)
        w.setLayout(layout)
        return w

    def _set_status(self, text):
        msg = f"status: {text}"
        self.status_label.setText(msg)
        self.status_label.setAccessibleName(msg)

    def _format_contact(self, rec):
        marker = "★ " if rec["favorite"] else "  "
        return f"{marker}{rec['first']} {rec['last']} <{rec['email']}>"

    def _create_contact(self, *, first, last, email, phone, category, notes, favorite):
        cid = self._next_id
        self._next_id += 1
        rec = {
            "id": cid,
            "first": first,
            "last": last,
            "email": email,
            "phone": phone,
            "category": category,
            "notes": notes,
            "favorite": bool(favorite),
        }
        self._contacts[cid] = rec
        item = QListWidgetItem(self._format_contact(rec))
        item.setData(Qt.ItemDataRole.UserRole, cid)
        self.contact_list.addItem(item)
        return cid

    def _selected_id(self):
        item = self.contact_list.currentItem()
        if item is None:
            return None
        return int(item.data(Qt.ItemDataRole.UserRole))

    def _populate_form(self, rec):
        self.first_name.setText(rec["first"])
        self.last_name.setText(rec["last"])
        self.email.setText(rec["email"])
        self.phone.setText(rec["phone"])
        idx = CATEGORIES.index(rec["category"]) if rec["category"] in CATEGORIES else 0
        self.category.setCurrentIndex(idx)
        self.notes.setPlainText(rec["notes"])
        self.favorite.setChecked(rec["favorite"])

    def _clear_form(self):
        self.first_name.clear()
        self.last_name.clear()
        self.email.clear()
        self.phone.clear()
        self.category.setCurrentIndex(0)
        self.notes.clear()
        self.favorite.setChecked(False)

    # -------------------------------------------------------------- slots

    def _on_selection_changed(self):
        cid = self._selected_id()
        if cid is None:
            return
        rec = self._contacts.get(cid)
        if rec is None:
            return
        self._populate_form(rec)
        self._set_status(f"selected {rec['first']} {rec['last']}")

    def _on_email_changed(self, text):
        if "@" in text:
            self.email_status.setText("✓ valid")
        elif text:
            self.email_status.setText("✗ missing @")
        else:
            self.email_status.setText("")

    def _on_search_changed(self, query):
        q = query.strip().lower()
        for i in range(self.contact_list.count()):
            item = self.contact_list.item(i)
            cid = int(item.data(Qt.ItemDataRole.UserRole))
            rec = self._contacts[cid]
            haystack = f"{rec['first']} {rec['last']} {rec['email']}".lower()
            visible = (not q) or (q in haystack)
            item.setHidden(not visible)
        if q:
            self._set_status(f"searching '{q}'")
        else:
            self._set_status("search cleared")

    def _on_clear_search(self):
        self.search_edit.clear()
        self._set_status("search cleared")

    def _on_add(self):
        cid = self._create_contact(
            first="",
            last="",
            email="",
            phone="",
            category=CATEGORIES[0],
            notes="",
            favorite=False,
        )
        # Select the new item.
        for i in range(self.contact_list.count()):
            item = self.contact_list.item(i)
            if int(item.data(Qt.ItemDataRole.UserRole)) == cid:
                self.contact_list.setCurrentItem(item)
                break
        self._set_status("new contact added")

    def _on_save(self):
        cid = self._selected_id()
        if cid is None:
            self._set_status("save: no selection")
            return
        # Validation: email must have @
        if "@" not in self.email.text():
            self._set_status("save: email invalid")
            return
        rec = self._contacts[cid]
        rec["first"] = self.first_name.text()
        rec["last"] = self.last_name.text()
        rec["email"] = self.email.text()
        rec["phone"] = self.phone.text()
        rec["category"] = self.category.currentText()
        rec["notes"] = self.notes.toPlainText()
        rec["favorite"] = self.favorite.isChecked()
        # Refresh list item label.
        item = self.contact_list.currentItem()
        if item is not None:
            item.setText(self._format_contact(rec))
        self._set_status(f"saved {rec['first']} {rec['last']}")

    def _on_cancel(self):
        cid = self._selected_id()
        if cid is None:
            self._clear_form()
            self._set_status("form cleared")
            return
        # Repopulate from store — discards unsaved changes.
        self._populate_form(self._contacts[cid])
        self._set_status("changes discarded")

    def _on_delete(self):
        cid = self._selected_id()
        if cid is None:
            self._set_status("delete: no selection")
            return
        rec = self._contacts.pop(cid, None)
        if rec is None:
            return
        # Remove from list.
        for i in range(self.contact_list.count()):
            item = self.contact_list.item(i)
            if int(item.data(Qt.ItemDataRole.UserRole)) == cid:
                self.contact_list.takeItem(i)
                break
        self._clear_form()
        if rec:
            self._set_status(f"deleted {rec['first']} {rec['last']}")


def main(argv=None):
    app = QApplication.instance() or QApplication(argv or sys.argv)
    win = ContactManager()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
