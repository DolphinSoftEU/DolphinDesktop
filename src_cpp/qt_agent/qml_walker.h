// Qt Quick / QML scene walker.
#pragma once

#include <QJsonArray>
#include <QJsonObject>
#include <QString>

#ifdef DOLPHIN_QT_AGENT_HAS_QML

namespace dolphin {

class QmlWalker
{
public:
    // Return every visible QQuickWindow's root item tree (handles included).
    static QJsonArray rootObjects();

    // Walk every QQuickItem in every visible QQuickWindow looking for a leaf
    // objectName match. Returns flat list of {handle, type, objectName, ...}.
    static QJsonArray findByObjectName(const QString& objectName);

    // Hit-test for a QQuickItem at given window-relative point.
    static QJsonObject itemAt(const QString& windowHandle, double x, double y);

    // Synthesize a click on a QQuickItem (mapped to its containing window).
    static QJsonObject click(const QString& itemHandle);
};

}  // namespace dolphin

#endif  // DOLPHIN_QT_AGENT_HAS_QML
