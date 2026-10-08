// QML scene walker — uses Qt's reflection on QQuickItem to enumerate the
// declarative tree. Built only when Qt Qml/Quick modules are present.

#include "qml_walker.h"

#ifdef DOLPHIN_QT_AGENT_HAS_QML

#include "object_walker.h"

#include <QApplication>
#include <QCoreApplication>
#include <QJsonObject>
#include <QPointF>
#include <QQmlComponent>
#include <QQuickItem>
#include <QQuickWindow>
#include <QVariant>
#include <QWindow>
#include <QMouseEvent>

namespace dolphin {

namespace {

QJsonObject describeItem(QQuickItem* item)
{
    QJsonObject node;
    node["handle"] = ObjectWalker::registerObject(item);
    node["type"] = QString::fromUtf8(item->metaObject()->className());
    node["objectName"] = item->objectName();
    node["visible"] = item->isVisible();
    node["enabled"] = item->isEnabled();
    node["x"] = item->x();
    node["y"] = item->y();
    node["width"] = item->width();
    node["height"] = item->height();

    static const char* kInteresting[] = {
        "text", "title", "value", "checked", "currentIndex", "currentText",
        "displayText", "placeholderText", "from", "to", "stepSize"
    };
    QJsonObject props;
    for (const char* name : kInteresting) {
        const QVariant v = item->property(name);
        if (v.isValid() && !v.toString().isEmpty()) {
            props[QString::fromUtf8(name)] = QJsonValue::fromVariant(v);
        } else if (v.isValid() && v.userType() == QMetaType::Bool) {
            props[QString::fromUtf8(name)] = v.toBool();
        } else if (v.isValid() && (v.userType() == QMetaType::Int
                                   || v.userType() == QMetaType::Double)) {
            props[QString::fromUtf8(name)] = QJsonValue::fromVariant(v);
        }
    }
    if (!props.isEmpty()) {
        node["properties"] = props;
    }

    QJsonArray kids;
    for (QQuickItem* child : item->childItems()) {
        kids.append(describeItem(child));
    }
    if (!kids.isEmpty()) {
        node["children"] = kids;
    }
    return node;
}

void collectAllItems(QQuickItem* root, QList<QQuickItem*>& out)
{
    if (!root) return;
    out.append(root);
    for (QQuickItem* child : root->childItems()) {
        collectAllItems(child, out);
    }
}

}  // namespace

QJsonArray QmlWalker::rootObjects()
{
    QJsonArray out;
    if (!qApp) return out;

    for (QWindow* w : qApp->topLevelWindows()) {
        QQuickWindow* qw = qobject_cast<QQuickWindow*>(w);
        if (!qw || !qw->isVisible()) continue;
        QJsonObject winNode;
        winNode["handle"] = ObjectWalker::registerObject(qw);
        winNode["type"] = QString::fromUtf8(qw->metaObject()->className());
        winNode["objectName"] = qw->objectName();
        winNode["title"] = qw->title();
        winNode["width"] = qw->width();
        winNode["height"] = qw->height();
        if (QQuickItem* root = qw->contentItem()) {
            winNode["root"] = describeItem(root);
        }
        out.append(winNode);
    }
    return out;
}

QJsonArray QmlWalker::findByObjectName(const QString& objectName)
{
    QJsonArray out;
    if (!qApp || objectName.isEmpty()) return out;

    for (QWindow* w : qApp->topLevelWindows()) {
        QQuickWindow* qw = qobject_cast<QQuickWindow*>(w);
        if (!qw) continue;
        QList<QQuickItem*> all;
        collectAllItems(qw->contentItem(), all);
        for (QQuickItem* item : all) {
            if (item->objectName() == objectName) {
                out.append(describeItem(item));
            }
        }
    }
    return out;
}

QJsonObject QmlWalker::itemAt(const QString& windowHandle, double x, double y)
{
    QJsonObject result;
    const QPointer<QObject> object_guard = ObjectWalker::resolveHandle(windowHandle);
    QObject* obj = object_guard.data();
    QQuickWindow* qw = qobject_cast<QQuickWindow*>(obj);
    if (!qw) {
        result["ok"] = false;
        result["error"] = QStringLiteral("not a QQuickWindow");
        return result;
    }
    QQuickItem* root = qw->contentItem();
    if (!root) {
        result["ok"] = false;
        result["error"] = QStringLiteral("no content item");
        return result;
    }
    // Walk depth-first; deepest visible child whose bounds contain the point wins.
    QQuickItem* hit = nullptr;
    QList<QQuickItem*> all;
    collectAllItems(root, all);
    for (QQuickItem* item : all) {
        if (!item->isVisible()) continue;
        const QPointF p = item->mapFromScene(QPointF(x, y));
        if (p.x() >= 0 && p.y() >= 0
            && p.x() < item->width() && p.y() < item->height()) {
            hit = item;  // keep last (deepest); list is parent-first
        }
    }
    if (!hit) {
        result["ok"] = false;
        result["error"] = QStringLiteral("no item at point");
        return result;
    }
    result["ok"] = true;
    result["item"] = describeItem(hit);
    return result;
}

QJsonObject QmlWalker::click(const QString& itemHandle)
{
    QJsonObject result;
    const QPointer<QObject> object_guard = ObjectWalker::resolveHandle(itemHandle);
    QObject* obj = object_guard.data();
    QQuickItem* item = qobject_cast<QQuickItem*>(obj);
    if (!item) {
        result["ok"] = false;
        result["error"] = QStringLiteral("not a QQuickItem");
        return result;
    }
    QQuickWindow* win = item->window();
    if (!win) {
        result["ok"] = false;
        result["error"] = QStringLiteral("item has no window");
        return result;
    }
    const QPointF localCenter(item->width() / 2.0, item->height() / 2.0);
    const QPointF sceneCenter = item->mapToScene(localCenter);

    QMouseEvent press(QEvent::MouseButtonPress, sceneCenter, sceneCenter,
                      win->mapToGlobal(sceneCenter.toPoint()),
                      Qt::LeftButton, Qt::LeftButton, Qt::NoModifier);
    QMouseEvent release(QEvent::MouseButtonRelease, sceneCenter, sceneCenter,
                        win->mapToGlobal(sceneCenter.toPoint()),
                        Qt::LeftButton, Qt::NoButton, Qt::NoModifier);
    QCoreApplication::sendEvent(win, &press);
    QCoreApplication::sendEvent(win, &release);
    result["ok"] = true;
    return result;
}

}  // namespace dolphin

#endif  // DOLPHIN_QT_AGENT_HAS_QML
