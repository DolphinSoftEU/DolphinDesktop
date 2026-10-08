// QObject tree walker — exposes Qt's meta-object system to Python.

#include "object_walker.h"

#include <QApplication>
#include <QByteArray>
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
#include <QVector>
#include <QWidget>
#include <QWindow>

#ifdef DOLPHIN_QT_AGENT_HAS_QML
#include <QQuickItem>
#include <QQuickWindow>
#endif

namespace dolphin {

namespace {

QMutex g_mutex;
struct HandleEntry
{
    QPointer<QObject> object;
    quint64 generation = 0;
};
QHash<QString, HandleEntry> g_handles;
QHash<QString, QString> g_current_handles;
quint64 g_next_generation = 0;

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
    const QString base_key = makeKey(obj);
    QMutexLocker lock(&g_mutex);
    auto current = g_current_handles.find(base_key);
    if (current != g_current_handles.end()) {
        const auto existing = g_handles.constFind(current.value());
        if (existing != g_handles.cend() && existing.value().object.data() == obj) {
            return current.value();
        }
    }
    const quint64 generation = ++g_next_generation;
    const QString key = QStringLiteral("%1#%2")
                            .arg(base_key)
                            .arg(static_cast<qulonglong>(generation));
    g_handles.insert(key, HandleEntry{QPointer<QObject>(obj), generation});
    g_current_handles.insert(base_key, key);
    // The generation prevents a delayed destroyed callback from removing a
    // new object that reused the same address and therefore the same handle.
    QObject::connect(obj, &QObject::destroyed, qApp, [base_key, key, generation]() {
            QMutexLocker l(&g_mutex);
            auto current = g_handles.find(key);
            if (current != g_handles.end() &&
                current.value().generation == generation) {
                g_handles.erase(current);
            }
            auto current_key = g_current_handles.find(base_key);
            if (current_key != g_current_handles.end() &&
                current_key.value() == key) {
                g_current_handles.erase(current_key);
            }
        });
    return key;
}

QPointer<QObject> ObjectWalker::resolveHandle(const QString& handle)
{
    QMutexLocker lock(&g_mutex);
    const auto it = g_handles.find(handle);
    if (it == g_handles.end()) return {};
    const QPointer<QObject> object = it.value().object;
    if (object.isNull()) g_handles.erase(it);
    return object;
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
    const QPointer<QObject> object_guard = resolveHandle(handle);
    QObject* obj = object_guard.data();
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
    const QPointer<QObject> object_guard = resolveHandle(handle);
    QObject* obj = object_guard.data();
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
    QGenericArgument arg() const
    {
        return QGenericArgument(variant.typeName(), variant.constData());
    }
};

bool coerce(const QJsonValue& jv, int targetTypeId, QVariant& converted)
{
    QVariant v = jv.toVariant();
    if (targetTypeId == QMetaType::UnknownType) return false;
    if (targetTypeId == QMetaType::QVariant || v.userType() == targetTypeId) {
        converted = v;
        return true;
    }
#if QT_VERSION >= QT_VERSION_CHECK(6, 0, 0)
    const bool ok = v.convert(QMetaType(targetTypeId));
#else
    const bool ok = v.convert(targetTypeId);
#endif
    if (!ok || v.userType() != targetTypeId) return false;
    converted = v;
    return true;
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
    const QPointer<QObject> object_guard = resolveHandle(handle);
    QObject* obj = object_guard.data();
    if (!obj) {
        result["ok"] = false;
        result["error"] = QStringLiteral("invalid handle");
        return result;
    }

    const bool has_signature = method.contains('(') && method.endsWith(')');
    const QByteArray requested_signature = has_signature
        ? QMetaObject::normalizedSignature(method.toUtf8().constData())
        : QByteArray();
    const QString methodName = has_signature
        ? method.left(method.indexOf('('))
        : method;
    const QMetaObject* mo = obj->metaObject();
    struct Candidate {
        QMetaMethod method;
        ArgSlot args[10];
    };
    QVector<Candidate> candidates;
    bool found_name_and_arity = false;
    bool conversion_failed = false;
    if (args.size() > 10) {
        result["ok"] = false;
        result["error"] = QStringLiteral("invoke supports at most 10 arguments");
        return result;
    }
    for (int i = 0; i < mo->methodCount(); ++i) {
        const QMetaMethod m = mo->method(i);
        if (m.parameterCount() != args.size()) continue;
        if (has_signature) {
            if (m.methodSignature() != requested_signature) continue;
        } else if (QString::fromUtf8(m.name()) != methodName) {
            continue;
        }
        found_name_and_arity = true;

        Candidate candidate;
        candidate.method = m;
        bool viable = true;
        for (int argIndex = 0; argIndex < args.size(); ++argIndex) {
            if (!coerce(args[argIndex], m.parameterType(argIndex),
                        candidate.args[argIndex].variant)) {
                viable = false;
                conversion_failed = true;
                break;
            }
        }
        if (viable) candidates.append(candidate);
    }

    if (candidates.size() > 1) {
        result["ok"] = false;
        result["error"] = QStringLiteral(
            "method '%1' is ambiguous; provide a full signature").arg(method);
        return result;
    }
    if (candidates.isEmpty()) {
        if (!found_name_and_arity && !has_signature && args.isEmpty()) {
            const bool ok = QMetaObject::invokeMethod(
                obj, methodName.toUtf8().constData(), Qt::DirectConnection);
            result["ok"] = ok;
            if (!ok) result["error"] = QStringLiteral("invokeMethod (no-arg) failed");
            return result;
        }
        result["ok"] = false;
        result["error"] = conversion_failed
            ? QStringLiteral("arguments cannot be converted for method '%1'").arg(method)
            : QStringLiteral("no method '%1' with %2 args").arg(method).arg(args.size());
        return result;
    }

    const Candidate& candidate = candidates.first();
    const QMetaMethod matched = candidate.method;
    const int n = args.size();

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
        n > 0 ? candidate.args[0].arg() : QGenericArgument(),
        n > 1 ? candidate.args[1].arg() : QGenericArgument(),
        n > 2 ? candidate.args[2].arg() : QGenericArgument(),
        n > 3 ? candidate.args[3].arg() : QGenericArgument(),
        n > 4 ? candidate.args[4].arg() : QGenericArgument(),
        n > 5 ? candidate.args[5].arg() : QGenericArgument(),
        n > 6 ? candidate.args[6].arg() : QGenericArgument(),
        n > 7 ? candidate.args[7].arg() : QGenericArgument(),
        n > 8 ? candidate.args[8].arg() : QGenericArgument(),
        n > 9 ? candidate.args[9].arg() : QGenericArgument());

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
    const QPointer<QObject> object_guard = resolveHandle(handle);
    QObject* obj = object_guard.data();
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
    const QPointer<QObject> object_guard = resolveHandle(handle);
    QObject* obj = object_guard.data();
    if (!obj) return QJsonValue();
    return QJsonValue::fromVariant(obj->property(prop.toUtf8().constData()));
}

}  // namespace dolphin
