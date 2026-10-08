// QObject tree walker and method invoker.
#pragma once

#include <QJsonArray>
#include <QJsonObject>
#include <QJsonValue>
#include <QString>

class QObject;

namespace dolphin {

class ObjectWalker
{
public:
    // Full tree of every top-level visible QWidget / QQuickWindow with depth-limited
    // recursion. Each node has: {handle, class, objectName, properties, children:[...]}.
    static QJsonArray topLevelTree();

    // Filter keys: "objectName", "className", "text", "regex" (regex over name).
    // Returns flat list of matching nodes.
    static QJsonArray find(const QJsonObject& filter);

    // Full property dump for one handle (every Q_PROPERTY of every meta class in chain).
    static QJsonObject describeFull(const QString& handle);

    // Invoke a method on a previously-handed-out handle.
    // Supports up to 4 args of common types: int / QString / bool / double / QVariant.
    static QJsonObject invoke(const QString& handle,
                              const QString& method,
                              const QJsonArray& args);

    // Q_PROPERTY accessors.
    static QJsonObject setProperty(const QString& handle,
                                   const QString& prop,
                                   const QJsonValue& value);
    static QJsonValue getProperty(const QString& handle, const QString& prop);

    // List of all Q_PROPERTY names + invokable method names for given handle.
    static QJsonObject listMembers(const QString& handle);

    // Lookup a stable handle for the QObject pointer (used by other walkers).
    static QString registerObject(QObject* obj);
    static QObject* resolveHandle(const QString& handle);
};

}  // namespace dolphin
