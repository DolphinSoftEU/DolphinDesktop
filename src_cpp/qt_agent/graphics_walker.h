// QGraphicsView / QGraphicsScene / QGraphicsItem walker.
// QGraphicsItem is NOT a QObject — uses its own (raw pointer) handle space
// with the "qgi@0xADDR" prefix to keep it distinct from QObject handles.
#pragma once

#include <QJsonArray>
#include <QJsonObject>
#include <QString>

namespace dolphin {

class GraphicsWalker
{
public:
    // Given a QGraphicsView handle (QObject handle), serialize its scene's
    // item tree. Returns [{handle, type, pos, rect, properties, children:[]}].
    static QJsonArray sceneItems(const QString& viewHandle);

    // Hit-test at scene coords.
    static QJsonObject itemAt(const QString& viewHandle, double sceneX, double sceneY);

    // Get/set graphics item property by name (QGraphicsObject-only — items that
    // inherit QObject also). Returns null for plain QGraphicsItem (no Q_PROPERTY).
    static QJsonValue getProperty(const QString& itemHandle, const QString& prop);
    static QJsonObject setProperty(const QString& itemHandle, const QString& prop,
                                   const QJsonValue& value);
};

}  // namespace dolphin
