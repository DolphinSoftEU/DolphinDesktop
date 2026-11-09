"""Qt tree AUT for the KAN-756 path tests: closed branches, duplicates, scrolling.

Run as ``_qt_tree_window.py pyqt5|pyside6 [first-column-width]``. Every
``itemClicked`` writes the clicked node's whole path to the status label, and
``itemExpanded`` does not, so a test can tell a selection from an opened branch.
"""

from __future__ import annotations

import sys

if sys.argv[1] == "pyqt5":
    from PyQt5.QtWidgets import (
        QApplication,
        QLabel,
        QMainWindow,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )
else:
    from PySide6.QtWidgets import (
        QApplication,
        QLabel,
        QMainWindow,
        QTreeWidget,
        QTreeWidgetItem,
        QVBoxLayout,
        QWidget,
    )

WINDOW_TITLE = "Dolphin Qt Tree Paths"


def _node(text: str, *children: QTreeWidgetItem) -> QTreeWidgetItem:
    item = QTreeWidgetItem([text, "node"])
    for child in children:
        item.addChild(child)
    return item


def _path_of(item: QTreeWidgetItem) -> str:
    names: list[str] = []
    current = item
    while current is not None:
        names.insert(0, current.text(0))
        current = current.parent()
    return "/".join(names)


def main() -> int:
    app = QApplication(sys.argv[:1])
    window = QMainWindow()
    window.setWindowTitle(WINDOW_TITLE)
    page = QWidget()
    layout = QVBoxLayout(page)
    status = QLabel("status: ready")
    layout.addWidget(status)

    tree = QTreeWidget()
    tree.setAccessibleName("Files")
    tree.setHeaderLabels(["Name", "Type"])
    tree.addTopLevelItem(
        _node(
            "Alpha",
            _node("src", _node("main.py"), _node("util.py")),
            _node("docs", _node("main.py")),
        )
    )
    deepest = _node("deep", _node("deeper", _node("deepest")))
    tree.addTopLevelItem(_node("Beta", _node("src", _node("main.py", deepest))))
    # A node named like the text every row shows in the second column.
    tree.addTopLevelItem(_node("node", _node("leaf")))
    for index in range(60):
        tree.addTopLevelItem(_node(f"Row {index:02d}", _node(f"Child {index:02d}")))
    tree.addTopLevelItem(_node("Omega", _node("Tail")))
    tree.topLevelItem(0).setExpanded(True)
    tree.setColumnWidth(0, int(sys.argv[2]) if len(sys.argv) > 2 else 200)
    tree.itemClicked.connect(lambda item, _column: status.setText(f"status: {_path_of(item)}"))

    layout.addWidget(tree)
    window.setCentralWidget(page)
    window.resize(520, 380)
    window.show()
    return app.exec() if hasattr(app, "exec") else app.exec_()


if __name__ == "__main__":
    sys.exit(main())
