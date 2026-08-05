"""Qt Charts demo — exercises the agent's polymorphic QObject introspection.

QChart and its series are QObject subclasses we don't link against from the
agent; everything works through the meta-object system. Useful to prove the
"works on any Qt module without rebuild" property.
"""

from __future__ import annotations

import sys

from PySide6.QtCharts import QChart, QChartView, QLineSeries
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication, QMainWindow


def main() -> int:
    app = QApplication(sys.argv)

    win = QMainWindow()
    win.setObjectName("chartsMainWindow")
    win.setWindowTitle("Dolphin Charts Demo")

    series = QLineSeries()
    series.setObjectName("priceSeries")
    series.setName("Price")
    for i in range(20):
        series.append(QPointF(i, (i * 17) % 30 + 5))

    chart = QChart()
    chart.setObjectName("priceChart")
    chart.setTitle("Sample price action")
    chart.addSeries(series)
    chart.createDefaultAxes()

    view = QChartView(chart)
    view.setObjectName("chartView")
    win.setCentralWidget(view)
    win.resize(640, 420)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
