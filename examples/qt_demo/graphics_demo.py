"""QGraphicsView demo — three named scene items driven by the
``qt_graphics`` marked tests in ``tests/qt/test_qt_graphics.py``."""

from __future__ import annotations

import sys

from PySide6.QtCore import QRectF
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsEllipseItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QMainWindow,
)


def main() -> int:
    app = QApplication(sys.argv)
    win = QMainWindow()
    win.setObjectName("graphicsMainWindow")
    win.setWindowTitle("Dolphin Graphics Demo")

    scene = QGraphicsScene()
    scene.setObjectName("demoScene")
    scene.setSceneRect(QRectF(0, 0, 500, 360))

    rect = QGraphicsRectItem(20, 20, 100, 60)
    rect.setBrush(QBrush(QColor("#cf7")))
    rect.setPen(QPen(QColor("#395")))
    rect.setData(0, "demoRect")
    scene.addItem(rect)

    circle = QGraphicsEllipseItem(180, 60, 80, 80)
    circle.setBrush(QBrush(QColor("#fc7")))
    circle.setPen(QPen(QColor("#953")))
    circle.setData(0, "demoCircle")
    scene.addItem(circle)

    label = QGraphicsTextItem("draggable text")
    label.setObjectName("demoText")
    label.setPos(30, 200)
    label.setFlag(QGraphicsTextItem.GraphicsItemFlag.ItemIsMovable, True)
    scene.addItem(label)

    view = QGraphicsView(scene)
    view.setObjectName("graphicsView")
    win.setCentralWidget(view)
    win.resize(560, 420)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
