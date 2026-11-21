// QGraphicsView / QGraphicsScene / QGraphicsItem walker.

#include "graphics_walker.h"
#include "object_walker.h"

#include <QGraphicsItem>
#include <QGraphicsObject>
#include <QGraphicsScene>
#include <QGraphicsView>
#include <QHash>
#include <QMutex>
#include <QObject>
#include <QPointF>
#include <QRectF>
#include <QString>
#include <QVariant>

namespace dolphin {

namespace {

// Plain QGraphicsItem isn't a QObject — separate handle space.
QMutex g_gMutex;
QHash<QString, QGraphicsItem*> g_gItems;

QString registerGraphicsItem(QGraphicsItem* item)
{
    if (!item) return {};
    const QString key = QStringLiteral("qgi@0x%1")
                            .arg(reinterpret_cast<quintptr>(item), 0, 16);
    QMutexLocker lock(&g_gMutex);
    g_gItems[key] = item;
    return key;
}

QGraphicsItem* resolveGraphicsItem(const QString& handle)
{
    QMutexLocker lock(&g_gMutex);
    const auto it = g_gItems.find(handle);
    return it == g_gItems.end() ? nullptr : it.value();
}

QJsonObject describeItem(QGraphicsItem* item)
{
    QJsonObject node;
    // If item is also a QGraphicsObject (QObject subclass), prefer QObject handle.
    if (QGraphicsObject* go = dynamic_cast<QGraphicsObject*>(item)) {
        node["handle"] = ObjectWalker::registerObject(go);
        node["type"] = QString::fromUtf8(go->metaObject()->className());
        node["objectName"] = go->objectName();
    } else {
        node["handle"] = registerGraphicsItem(item);
        node["type"] = QStringLiteral("QGraphicsItem");
    }
    const QPointF p = item->pos();
    node["x"] = p.x();
    node["y"] = p.y();
    const QRectF r = item->boundingRect();
    node["w"] = r.width();
    node["h"] = r.height();
    node["visible"] = item->isVisible();
    node["enabled"] = item->isEnabled();
    node["selected"] = item->isSelected();
    node["zValue"] = item->zValue();
    node["type_enum"] = item->type();

    QJsonArray kids;
    for (QGraphicsItem* child : item->childItems()) {
        kids.append(describeItem(child));
    }
    if (!kids.isEmpty()) {
        node["children"] = kids;
    }
    return node;
}

}  // namespace

QJsonArray GraphicsWalker::sceneItems(const QString& viewHandle)
{
    QJsonArray out;
    const QPointer<QObject> object_guard = ObjectWalker::resolveHandle(viewHandle);
    QObject* obj = object_guard.data();
    QGraphicsView* view = qobject_cast<QGraphicsView*>(obj);
    if (!view) return out;

    QGraphicsScene* scene = view->scene();
    if (!scene) return out;

    // Top-level items (those without a parent in the scene).
    for (QGraphicsItem* item : scene->items()) {
        if (!item->parentItem()) {
            out.append(describeItem(item));
        }
    }
    return out;
}

QJsonObject GraphicsWalker::itemAt(const QString& viewHandle, double sceneX, double sceneY)
{
    QJsonObject result;
    const QPointer<QObject> object_guard = ObjectWalker::resolveHandle(viewHandle);
    QObject* obj = object_guard.data();
    QGraphicsView* view = qobject_cast<QGraphicsView*>(obj);
    if (!view || !view->scene()) {
        result["ok"] = false;
        result["error"] = QStringLiteral("not a QGraphicsView or no scene");
        return result;
    }
    QGraphicsItem* hit = view->scene()->itemAt(QPointF(sceneX, sceneY), QTransform());
    if (!hit) {
        result["ok"] = false;
        result["error"] = QStringLiteral("no item at point");
        return result;
    }
    result["ok"] = true;
    result["item"] = describeItem(hit);
    return result;
}

QJsonValue GraphicsWalker::getProperty(const QString& itemHandle, const QString& prop)
{
    if (itemHandle.startsWith("qgi@")) {
        // Raw QGraphicsItem has no Q_PROPERTY system.
        return QJsonValue();
    }
    return ObjectWalker::getProperty(itemHandle, prop);
}

QJsonObject GraphicsWalker::setProperty(const QString& itemHandle, const QString& prop,
                                       const QJsonValue& value)
{
    QJsonObject result;
    if (itemHandle.startsWith("qgi@")) {
        result["ok"] = false;
        result["error"] = QStringLiteral("plain QGraphicsItem has no Q_PROPERTY");
        return result;
    }
    return ObjectWalker::setProperty(itemHandle, prop, value);
}

}  // namespace dolphin
