"""Loader for examples/qt_demo/qml_widgets.qml.

Run::
    python -m examples.qt_demo.qml_widgets
or
    .venv/Scripts/python.exe examples/qt_demo/qml_widgets.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine


def main() -> int:
    app = QGuiApplication(sys.argv)
    engine = QQmlApplicationEngine()
    qml_path = Path(__file__).resolve().with_name("qml_widgets.qml")
    engine.load(QUrl.fromLocalFile(str(qml_path)))
    if not engine.rootObjects():
        print(f"QML failed to load: {qml_path}", file=sys.stderr)
        return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
