// QObject tree walker — exposes Qt's meta-object system to Python.

#include "object_walker.h"

#include <QApplication>
#include <QGraphicsItem>
#include <QGraphicsObject>
#include <QGraphicsScene>
#include <QGraphicsView>
#include <QGuiApplication>
#include <QHash>
#include <QMetaMethod>
#include <QMetaObject>
#include <QMetaProperty>
#include <QMutex>
#include <QObject>
#include <QRegularExpression>
#include <QString>
#include <QVariant>
#include <QWidget>
#include <QWindow>

#ifdef DOLPHIN_QT_AGENT_HAS_QML
#include <QQuickItem>
#include <QQuickWindow>
#endif

namespace dolphin {

namespace {

QMutex g_mutex;
QHash<QString, QObject*> g_handles;

QString makeKey(QObject* obj)
{
    return QStringLiteral("%1@0x%2")
        .arg(QString::fromUtf8(obj->metaObject()->className()))
        .arg(reinterpret_cast<quintptr>(obj), 0, 16);
}

}  // namespace

QString ObjectWalker::registerObject(QObject* obj)
{
    if (!obj) return {};
    const QString key = makeKey(obj);
    QMutexLocker lock(&g_mutex);
    if (!g_handles.contains(key)) {
        g_handles.insert(key, obj);
        // Best-effort cleanup on destroy. Using a lambda with captured key
        // means even if the QObject lives in another thread, the connect
        // queues into our worker.
        QObject::connect(obj, &QObject::destroyed, qApp, [key]() {
            QMutexLocker l(&g_mutex);
            g_handles.remove(key);
        });
    }
    return key;
}

QObject* ObjectWalker::resolveHandle(const QString& handle)
{
    QMutexLocker lock(&g_mutex);
    const auto it = g_handles.find(handle);
    return it == g_handles.end() ? nullptr : it.value();
}

namespace {

QJsonObject describe(QObject* obj, int depth);

QJsonValue extractInterestingProperties(QObject* obj)
{
    const QMetaObject* mo = obj->metaObject();
    static const char* kInteresting[] = {
        "text", "title", "windowTitle", "enabled", "visible", "checked",
        "value", "currentText", "currentIndex", "displayText", "placeholderText",
        "readOnly", "checkState", "minimum", "maximum"
    };
    QJsonObject props;
    for (const char* name : kInteresting) {
        const int idx = mo->indexOfProperty(name);
        if (idx < 0) continue;
        const QMetaProperty p = mo->property(idx);
        const QVariant v = p.read(obj);
        if (v.isValid()) {
            props[QString::fromUtf8(name)] = QJsonValue::fromVariant(v);
        }
    }
    return props.isEmpty() ? QJsonValue() : QJsonValue(props);
}

QJsonObject describe(QObject* obj, int depth)
{
    QJsonObject node;
    node["handle"] = ObjectWalker::registerObject(obj);
    node["class"] = QString::fromUtf8(obj->metaObject()->className());
    node["objectName"] = obj->objectName();

    if (QWidget* w = qobject_cast<QWidget*>(obj)) {
        node["visible"] = w->isVisible();
        node["enabled"] = w->isEnabled();
        const QRect g = w->geometry();
        node["geometry"] = QJsonObject{
            {"x", g.x()}, {"y", g.y()},
            {"w", g.width()}, {"h", g.height()}};
    }

    const QJsonValue props = extractInterestingProperties(obj);
    if (!props.isNull()) {
        node["properties"] = props;
    }

    if (depth > 0) {
        QJsonArray kids;
        for (QObject* child : obj->children()) {
            kids.append(describe(child, depth - 1));
        }
        if (!kids.isEmpty()) {
            node["children"] = kids;
        }
    }
    return node;
}

}  // namespace

QJsonArray ObjectWalker::topLevelTree()
{
    QJsonArray out;
    if (!qApp) return out;

    // QWidget top-levels.
    for (QWidget* top : QApplication::topLevelWidgets()) {
        if (top && top->isVisible()) {
            out.append(describe(top, /*depth=*/12));
        }
    }
#ifdef DOLPHIN_QT_AGENT_HAS_QML
    // QQuickWindow top-levels (separate from QWidget hierarchy).
    for (QWindow* w : qApp->topLevelWindows()) {
        if (!w || !w->isVisible()) continue;
        if (qobject_cast<QQuickWindow*>(w)) {
            out.append(describe(w, /*depth=*/12));
        }
    }
#endif
    return out;
}

QJsonArray ObjectWalker::find(const QJsonObject& filter)
{
    QJsonArray out;
    if (!qApp) return out;

    const QString objectName = filter.value("objectName").toString();
    const QString className = filter.value("className").toString();
    const QString text = filter.value("text").toString();
    const QString regexStr = filter.value("regex").toString();
    QRegularExpression re;
    if (!regexStr.isEmpty()) {
        re.setPattern(regexStr);
    }

    auto matches = [&](QObject* obj) {
        if (!obj) return false;
        if (!objectName.isEmpty() && obj->objectName() != objectName) {
            return false;
        }
        if (!className.isEmpty()) {
            const QString cls = QString::fromUtf8(obj->metaObject()->className());
            if (cls != className) {
                // Also check inheritance chain.
                const QMetaObject* mo = obj->metaObject();
                bool inherits = false;
                while (mo) {
                    if (QString::fromUtf8(mo->className()) == className) {
                        inherits = true;
                        break;
                    }
                    mo = mo->superClass();
                }
                if (!inherits) return false;
            }
        }
        if (!text.isEmpty()) {
            const QVariant t = obj->property("text");
            if (!t.isValid() || t.toString() != text) {
                const QVariant title = obj->property("title");
                if (!title.isValid() || title.toString() != text) {
                    return false;
                }
            }
        }
        if (!regexStr.isEmpty() && re.isValid()) {
            if (!re.match(obj->objectName()).hasMatch()) {
                return false;
            }
        }
        return true;
    };

    auto scan = [&](QObject* root) {
        if (matches(root)) {
            out.append(describe(root, 0));
        }
        for (QObject* obj : root->findChildren<QObject*>()) {
            if (matches(obj)) {
                out.append(describe(obj, 0));
            }
            // If this is a QGraphicsView, also scan its scene's items.
            if (QGraphicsView* gv = qobject_cast<QGraphicsView*>(obj)) {
                if (QGraphicsScene* scene = gv->scene()) {
                    // Scene itself can match.
                    if (matches(scene)) {
                        out.append(describe(scene, 0));
                    }
                    for (QObject* sceneChild : scene->findChildren<QObject*>()) {
                        if (matches(sceneChild)) {
                            out.append(describe(sceneChild, 0));
                        }
                    }
                    // Walk QGraphicsItems — those that are also QGraphicsObject
                    // (QObject subclass) can be matched. Plain QGraphicsItem cannot.
                    for (QGraphicsItem* item : scene->items()) {
                        if (QGraphicsObject* go =
                                dynamic_cast<QGraphicsObject*>(item)) {
                            if (matches(go)) {
                                out.append(describe(go, 0));
                            }
                        }
                    }
                }
            }
        }
    };

    for (QWidget* top : QApplication::topLevelWidgets()) {
        scan(top);
    }
#ifdef DOLPHIN_QT_AGENT_HAS_QML
    for (QWindow* w : qApp->topLevelWindows()) {
        if (qobject_cast<QQuickWindow*>(w)) {
            scan(w);
        }
    }
#endif
    return out;
}

QJsonObject ObjectWalker::describeFull(const QString& handle)
{
    QJsonObject result;
    QObject* obj = resolveHandle(handle);
    if (!obj) {
        result["ok"] = false;
        result["error"] = QStringLiteral("invalid handle");
        return result;
    }
    const QMetaObject* mo = obj->metaObject();
    QJsonObject props;
    for (int i = 0; i < mo->propertyCount(); ++i) {
        const QMetaProperty p = mo->property(i);
        const QVariant v = p.read(obj);
        if (v.isValid()) {
            props[QString::fromUtf8(p.name())] = QJsonValue::fromVariant(v);
        }
    }
    result["ok"] = true;
    result["class"] = QString::fromUtf8(mo->className());
    result["properties"] = props;
    return result;
}

QJsonObject ObjectWalker::listMembers(const QString& handle)
{
    QJsonObject result;
    QObject* obj = resolveHandle(handle);
    if (!obj) {
        result["ok"] = false;
        result["error"] = QStringLiteral("invalid handle");
        return result;
    }
    const QMetaObject* mo = obj->metaObject();
    QJsonArray props;
    for (int i = 0; i < mo->propertyCount(); ++i) {
        props.append(QString::fromUtf8(mo->property(i).name()));
    }
    QJsonArray methods;
    for (int i = 0; i < mo->methodCount(); ++i) {
        const QMetaMethod m = mo->method(i);
        if (m.methodType() == QMetaMethod::Slot
            || m.methodType() == QMetaMethod::Method) {
            methods.append(QString::fromUtf8(m.methodSignature()));
        }
    }
    QJsonArray signals_;
    for (int i = 0; i < mo->methodCount(); ++i) {
        const QMetaMethod m = mo->method(i);
        if (m.methodType() == QMetaMethod::Signal) {
            signals_.append(QString::fromUtf8(m.methodSignature()));
        }
    }
    result["ok"] = true;
    result["properties"] = props;
    result["methods"] = methods;
    result["signals"] = signals_;
    return result;
}

namespace {

// Convert a QVariant into a QGenericArgument-compatible storage slot,
// dispatching on the target method's parameterType.
// Storage must outlive the invokeMethod call — we return a QVariant whose
// underlying type matches what Qt expects, then take its address.
struct ArgSlot {
    QVariant variant;
    QGenericArgument arg() { return QGenericArgument(variant.typeName(), variant.data()); }
};

QVariant coerce(const QJsonValue& jv, int targetTypeId)
{
    QVariant v = jv.toVariant();
    if (targetTypeId == QMetaType::UnknownType) return v;
    if (v.userType() == targetTypeId) return v;
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    v.convert(QMetaType(targetTypeId));
#else
    v.convert(targetTypeId);
#endif
    return v;
}

QVariant makeEmptyVariant(int typeId)
{
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    return QVariant(QMetaType(typeId));
#else
    return QVariant(typeId, nullptr);
#endif
}

}  // namespace

QJsonObject ObjectWalker::invoke(const QString& handle,
                                 const QString& method,
                                 const QJsonArray& args)
{
    QJsonObject result;
    QObject* obj = resolveHandle(handle);
    if (!obj) {
        result["ok"] = false;
        result["error"] = QStringLiteral("invalid handle");
        return result;
    }

    // Strip "(...)" if present.
    QString methodName = method;
    if (methodName.endsWith(')')) {
        methodName = methodName.left(methodName.indexOf('('));
    }

    // Locate matching method by name + arg count.
    const QMetaObject* mo = obj->metaObject();
    int matchIdx = -1;
    QMetaMethod matched;
    for (int i = 0; i < mo->methodCount(); ++i) {
        const QMetaMethod m = mo->method(i);
        if (QString::fromUtf8(m.name()) == methodName
            && m.parameterCount() == args.size()) {
            matchIdx = i;
            matched = m;
            break;
        }
    }

    if (matchIdx < 0) {
        // Fall back to no-arg invoke (covers methods not registered as Q_INVOKABLE
        // but accessible via QMetaObject::invokeMethod by name).
        if (args.isEmpty()) {
            const bool ok = QMetaObject::invokeMethod(
                obj, methodName.toUtf8().constData(), Qt::DirectConnection);
            result["ok"] = ok;
            if (!ok) {
                result["error"] = QStringLiteral("invokeMethod (no-arg) failed");
            }
            return result;
        }
        result["ok"] = false;
        result["error"] = QStringLiteral("no method '%1' with %2 args")
                              .arg(methodName)
                              .arg(args.size());
        return result;
    }

    // Coerce args using parameter types. Note: don't name this 'slots' —
    // that's a Qt Q_SLOTS keyword via moc preprocessor.
    ArgSlot arg_slots[10];
    int n = qMin<int>(args.size(), 10);
    for (int i = 0; i < n; ++i) {
        arg_slots[i].variant = coerce(args[i], matched.parameterType(i));
    }

    QVariant returnVar;
    QGenericReturnArgument ret;
    if (matched.returnType() != QMetaType::Void
        && matched.returnType() != QMetaType::UnknownType) {
        returnVar = makeEmptyVariant(matched.returnType());
        ret = QGenericReturnArgument(returnVar.typeName(), returnVar.data());
    }

    bool ok = matched.invoke(
        obj,
        Qt::DirectConnection,
        ret,
        n > 0 ? arg_slots[0].arg() : QGenericArgument(),
        n > 1 ? arg_slots[1].arg() : QGenericArgument(),
        n > 2 ? arg_slots[2].arg() : QGenericArgument(),
        n > 3 ? arg_slots[3].arg() : QGenericArgument(),
        n > 4 ? arg_slots[4].arg() : QGenericArgument(),
        n > 5 ? arg_slots[5].arg() : QGenericArgument(),
        n > 6 ? arg_slots[6].arg() : QGenericArgument(),
        n > 7 ? arg_slots[7].arg() : QGenericArgument(),
        n > 8 ? arg_slots[8].arg() : QGenericArgument(),
        n > 9 ? arg_slots[9].arg() : QGenericArgument());

    result["ok"] = ok;
    if (returnVar.isValid()) {
        result["result"] = QJsonValue::fromVariant(returnVar);
    }
    if (!ok) {
        result["error"] = QStringLiteral("invoke failed");
    }
    return result;
}

QJsonObject ObjectWalker::setProperty(const QString& handle,
                                      const QString& prop,
                                      const QJsonValue& value)
{
    QJsonObject result;
    QObject* obj = resolveHandle(handle);
    if (!obj) {
        result["ok"] = false;
        result["error"] = QStringLiteral("invalid handle");
        return result;
    }
    // Coerce to the property's declared type for better cross-type fidelity.
    const QMetaObject* mo = obj->metaObject();
    const int idx = mo->indexOfProperty(prop.toUtf8().constData());
    QVariant v = value.toVariant();
    if (idx >= 0) {
        const QMetaProperty p = mo->property(idx);
        const int targetType = p.userType();
        if (v.userType() != targetType) {
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
            v.convert(QMetaType(targetType));
#else
            v.convert(targetType);
#endif
        }
    }
    const bool ok = obj->setProperty(prop.toUtf8().constData(), v);
    result["ok"] = ok;
    return result;
}

QJsonValue ObjectWalker::getProperty(const QString& handle, const QString& prop)
{
    QObject* obj = resolveHandle(handle);
    if (!obj) return QJsonValue();
    return QJsonValue::fromVariant(obj->property(prop.toUtf8().constData()));
}

}  // namespace dolphin
