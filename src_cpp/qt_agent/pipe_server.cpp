// Authenticated, bounded named-pipe JSON server for the dolphin Qt agent.

#include "pipe_server.h"

#include "graphics_walker.h"
#include "object_walker.h"
#include "qml_walker.h"

#include <QApplication>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMetaObject>
#include <QThread>

#include <algorithm>
#include <chrono>
#include <cstring>

#ifdef _WIN32
#include <windows.h>
#include <sddl.h>
#endif

namespace dolphin {

namespace {

constexpr qsizetype kMaxRequestBytes = 8 * 1024 * 1024;
constexpr qsizetype kMaxResponseBytes = 16 * 1024 * 1024;
constexpr int kMaxJsonDepth = 32;
constexpr int kMaxJsonNodes = 10000;
constexpr auto kRequestTimeout = std::chrono::seconds(30);
constexpr auto kIdleTimeout = std::chrono::seconds(60);
constexpr auto kWriteTimeout = std::chrono::seconds(30);
constexpr DWORD kPollMs = 5;

// Dispatch a callable onto the GUI thread and block until it returns.
template <typename Fn>
QJsonValue run_on_gui_thread(Fn&& fn)
{
    QJsonValue result;
    if (QThread::currentThread() == qApp->thread()) {
        result = fn();
    } else {
        QMetaObject::invokeMethod(
            qApp,
            [&]() { result = fn(); },
            Qt::BlockingQueuedConnection);
    }
    return result;
}

QJsonObject handle_request(const QJsonObject& req)
{
    QJsonObject resp;
    resp["id"] = req.value("id");
    const QString op = req.value("op").toString();

    try {
        if (op == "ping") {
            resp["ok"] = true;
            resp["result"] = QStringLiteral("pong");
        } else if (op == "tree") {
            resp["ok"] = true;
            resp["result"] = run_on_gui_thread([]() {
                return QJsonValue(ObjectWalker::topLevelTree());
            });
        } else if (op == "find") {
            const QJsonObject filter = req.value("filter").toObject();
            resp["ok"] = true;
            resp["result"] = run_on_gui_thread([&]() {
                return QJsonValue(ObjectWalker::find(filter));
            });
        } else if (op == "describe") {
            const QString target = req.value("target").toString();
            resp["ok"] = true;
            resp["result"] = run_on_gui_thread([&]() {
                return QJsonValue(ObjectWalker::describeFull(target));
            });
        } else if (op == "members") {
            const QString target = req.value("target").toString();
            resp["ok"] = true;
            resp["result"] = run_on_gui_thread([&]() {
                return QJsonValue(ObjectWalker::listMembers(target));
            });
        } else if (op == "invoke") {
            const QString target = req.value("target").toString();
            const QString method = req.value("method").toString();
            const QJsonArray args = req.value("args").toArray();
            resp["ok"] = true;
            resp["result"] = run_on_gui_thread([&]() {
                return QJsonValue(ObjectWalker::invoke(target, method, args));
            });
        } else if (op == "set_property") {
            const QString target = req.value("target").toString();
            const QString prop = req.value("property").toString();
            const QJsonValue value = req.value("value");
            resp["ok"] = true;
            resp["result"] = run_on_gui_thread([&]() {
                return QJsonValue(ObjectWalker::setProperty(target, prop, value));
            });
        } else if (op == "get_property") {
            const QString target = req.value("target").toString();
            const QString prop = req.value("property").toString();
            resp["ok"] = true;
            resp["result"] = run_on_gui_thread([&]() {
                return ObjectWalker::getProperty(target, prop);
            });
        }
#ifdef DOLPHIN_QT_AGENT_HAS_QML
        else if (op == "qml_root") {
            resp["ok"] = true;
            resp["result"] = run_on_gui_thread([]() {
                return QJsonValue(QmlWalker::rootObjects());
            });
        } else if (op == "qml_find") {
            const QString name = req.value("objectName").toString();
            resp["ok"] = true;
            resp["result"] = run_on_gui_thread([&]() {
                return QJsonValue(QmlWalker::findByObjectName(name));
            });
        } else if (op == "qml_item_at") {
            const QString winH = req.value("window").toString();
            const double x = req.value("x").toDouble();
            const double y = req.value("y").toDouble();
            resp["ok"] = true;
            resp["result"] = run_on_gui_thread([&]() {
                return QJsonValue(QmlWalker::itemAt(winH, x, y));
            });
        } else if (op == "qml_click") {
            const QString item = req.value("target").toString();
            resp["ok"] = true;
            resp["result"] = run_on_gui_thread([&]() {
                return QJsonValue(QmlWalker::click(item));
            });
        }
#endif
        else if (op == "graphics_items") {
            const QString view = req.value("view").toString();
            resp["ok"] = true;
            resp["result"] = run_on_gui_thread([&]() {
                return QJsonValue(GraphicsWalker::sceneItems(view));
            });
        } else if (op == "graphics_item_at") {
            const QString view = req.value("view").toString();
            const double x = req.value("x").toDouble();
            const double y = req.value("y").toDouble();
            resp["ok"] = true;
            resp["result"] = run_on_gui_thread([&]() {
                return QJsonValue(GraphicsWalker::itemAt(view, x, y));
            });
        } else {
            resp["ok"] = false;
            resp["error"] = QString("unknown op: %1").arg(op);
        }
    } catch (const std::exception& exc) {
        resp["ok"] = false;
        resp["error"] = QString::fromUtf8(exc.what());
    } catch (...) {
        resp["ok"] = false;
        resp["error"] = QStringLiteral("unknown exception");
    }
    return resp;
}

bool validate_shape(const QJsonValue& value, int depth, int& nodes)
{
    if (depth > kMaxJsonDepth || ++nodes > kMaxJsonNodes) {
        return false;
    }
    if (value.isArray()) {
        for (const QJsonValue& child : value.toArray()) {
            if (!validate_shape(child, depth + 1, nodes)) return false;
        }
    } else if (value.isObject()) {
        const QJsonObject obj = value.toObject();
        for (auto it = obj.constBegin(); it != obj.constEnd(); ++it) {
            if (++nodes > kMaxJsonNodes ||
                !validate_shape(it.value(), depth + 1, nodes)) {
                return false;
            }
        }
    }
    return true;
}

// Enforce nesting and a coarse node limit before handing attacker-controlled
// bytes to Qt's JSON parser. The post-parse validator below checks the exact
// JSON value tree as a second line of defense.
bool raw_json_within_limits(const QByteArray& json)
{
    int depth = 0;
    int nodes = 0;
    bool in_string = false;
    bool escaped = false;
    bool in_scalar = false;

    for (const char ch : json) {
        if (in_string) {
            if (escaped) {
                escaped = false;
            } else if (ch == '\\') {
                escaped = true;
            } else if (ch == '"') {
                in_string = false;
            }
            continue;
        }

        if (ch == '"') {
            in_string = true;
            in_scalar = false;
            if (++nodes > kMaxJsonNodes) return false;
        } else if (ch == '{' || ch == '[') {
            in_scalar = false;
            if (++depth > kMaxJsonDepth || ++nodes > kMaxJsonNodes) return false;
        } else if (ch == '}' || ch == ']') {
            in_scalar = false;
            if (--depth < 0) return false;
        } else if (ch == ',' || ch == ':' || ch == ' ' || ch == '\t' ||
                   ch == '\r' || ch == '\n') {
            in_scalar = false;
        } else if (!in_scalar) {
            in_scalar = true;
            if (++nodes > kMaxJsonNodes) return false;
        }
    }
    return !in_string && depth == 0;
}

bool constant_time_equal(const QByteArray& supplied, const QByteArray& expected)
{
    unsigned char diff = static_cast<unsigned char>(supplied.size() ^ expected.size());
    const qsizetype count = std::min(supplied.size(), expected.size());
    for (qsizetype i = 0; i < count; ++i) {
        diff |= static_cast<unsigned char>(supplied.at(i) ^ expected.at(i));
    }
    return diff == 0 && supplied.size() == expected.size();
}

#ifdef _WIN32

QString current_logon_sid()
{
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return {};

    DWORD bytes = 0;
    GetTokenInformation(token, TokenGroups, nullptr, 0, &bytes);
    if (bytes == 0) {
        CloseHandle(token);
        return {};
    }
    QByteArray storage(static_cast<int>(bytes), '\0');
    const BOOL ok = GetTokenInformation(
        token, TokenGroups, storage.data(), bytes, &bytes);
    CloseHandle(token);
    if (!ok) return {};

    const auto* groups = reinterpret_cast<const TOKEN_GROUPS*>(storage.constData());
    for (DWORD i = 0; i < groups->GroupCount; ++i) {
        const SID_AND_ATTRIBUTES& group = groups->Groups[i];
        if ((group.Attributes & SE_GROUP_LOGON_ID) != SE_GROUP_LOGON_ID) continue;
        LPWSTR sid_text = nullptr;
        if (!ConvertSidToStringSidW(group.Sid, &sid_text)) return {};
        const QString sid = QString::fromWCharArray(sid_text);
        LocalFree(sid_text);
        return sid;
    }
    return {};
}

bool make_pipe_security(QString& sid, PSECURITY_DESCRIPTOR& descriptor,
                        SECURITY_ATTRIBUTES& attributes)
{
    sid = current_logon_sid();
    if (sid.isEmpty()) return false;
    const QString sddl = QStringLiteral("D:P(A;;GA;;;%1)").arg(sid);
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(
            reinterpret_cast<LPCWSTR>(sddl.utf16()), SDDL_REVISION_1,
            &descriptor, nullptr)) {
        return false;
    }
    attributes.nLength = sizeof(SECURITY_ATTRIBUTES);
    attributes.lpSecurityDescriptor = descriptor;
    attributes.bInheritHandle = FALSE;
    return true;
}

enum class ReadResult { Line, Closed, TimedOut, TooLarge, Error };

ReadResult read_line(HANDLE pipe, std::atomic<bool>& stop,
                     QByteArray& pending, QByteArray& line)
{
    using Clock = std::chrono::steady_clock;
    auto last_activity = Clock::now();
    auto line_started = last_activity;

    while (!stop) {
        const qsizetype newline = pending.indexOf('\n');
        if (newline >= 0) {
            if (newline + 1 > kMaxRequestBytes) return ReadResult::TooLarge;
            line = pending.left(newline);
            pending.remove(0, newline + 1);
            return ReadResult::Line;
        }
        if (pending.size() > kMaxRequestBytes) return ReadResult::TooLarge;

        const auto now = Clock::now();
        if (pending.isEmpty()) {
            if (now - last_activity >= kIdleTimeout) return ReadResult::TimedOut;
        } else if (now - line_started >= kRequestTimeout) {
            return ReadResult::TimedOut;
        }

        DWORD available = 0;
        if (!PeekNamedPipe(pipe, nullptr, 0, nullptr, &available, nullptr)) {
            const DWORD error = GetLastError();
            return error == ERROR_BROKEN_PIPE || error == ERROR_PIPE_NOT_CONNECTED
                ? ReadResult::Closed : ReadResult::Error;
        }
        if (available == 0) {
            Sleep(kPollMs);
            continue;
        }

        char chunk[4096];
        const DWORD request = std::min<DWORD>(available, sizeof(chunk));
        DWORD read = 0;
        if (!ReadFile(pipe, chunk, request, &read, nullptr)) {
            const DWORD error = GetLastError();
            if (error == ERROR_NO_DATA) {
                Sleep(kPollMs);
                continue;
            }
            return error == ERROR_BROKEN_PIPE || error == ERROR_PIPE_NOT_CONNECTED
                ? ReadResult::Closed : ReadResult::Error;
        }
        if (read == 0) return ReadResult::Closed;
        if (pending.isEmpty()) line_started = Clock::now();
        pending.append(chunk, static_cast<qsizetype>(read));
        last_activity = Clock::now();
    }
    return ReadResult::Closed;
}

bool write_line(HANDLE pipe, const QByteArray& data)
{
    QByteArray out = data;
    out.append('\n');
    qsizetype offset = 0;
    const auto deadline = std::chrono::steady_clock::now() + kWriteTimeout;
    while (offset < out.size()) {
        const DWORD count = static_cast<DWORD>(std::min<qsizetype>(4096, out.size() - offset));
        DWORD written = 0;
        if (WriteFile(pipe, out.constData() + offset, count, &written, nullptr)) {
            if (written == 0) return false;
            offset += static_cast<qsizetype>(written);
            continue;
        }
        const DWORD error = GetLastError();
        if (error != ERROR_NO_DATA && error != ERROR_PIPE_BUSY) return false;
        if (std::chrono::steady_clock::now() >= deadline) return false;
        Sleep(kPollMs);
    }
    return true;
}

bool expected_client(HANDLE pipe, quint32 client_pid)
{
    ULONG connected_pid = 0;
    if (!GetNamedPipeClientProcessId(pipe, &connected_pid)) return false;
    return connected_pid == client_pid;
}

#endif  // _WIN32

}  // namespace

PipeServer::PipeServer(QString pipe_name, QString session_secret, quint32 client_pid)
    : pipe_name_(std::move(pipe_name)),
      session_secret_(std::move(session_secret)),
      client_pid_(client_pid)
{}

PipeServer::~PipeServer() = default;

void PipeServer::stop() { stop_ = true; }

void PipeServer::run()
{
#ifdef _WIN32
    QString logon_sid;
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    SECURITY_ATTRIBUTES attributes{};
    if (!make_pipe_security(logon_sid, descriptor, attributes)) return;

    const QByteArray expected_secret = session_secret_.toUtf8();
    QByteArray pending;
    const auto pipe_path = reinterpret_cast<LPCWSTR>(pipe_name_.utf16());

    while (!stop_) {
        HANDLE pipe = CreateNamedPipeW(
            pipe_path,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_NOWAIT |
                PIPE_REJECT_REMOTE_CLIENTS,
            1,
            64 * 1024,
            64 * 1024,
            0,
            &attributes);
        if (pipe == INVALID_HANDLE_VALUE) break;

        bool connected = false;
        while (!stop_) {
            if (ConnectNamedPipe(pipe, nullptr)) {
                connected = true;
                break;
            }
            const DWORD error = GetLastError();
            if (error == ERROR_PIPE_CONNECTED) {
                connected = true;
                break;
            }
            if (error != ERROR_PIPE_LISTENING && error != ERROR_NO_DATA) break;
            Sleep(kPollMs);
        }

        if (connected && expected_client(pipe, client_pid_)) {
            pending.clear();
            while (!stop_) {
                QByteArray line;
                const ReadResult read_result = read_line(pipe, stop_, pending, line);
                if (read_result != ReadResult::Line) break;

                QJsonParseError parse_error;
                QJsonObject response;
                if (!raw_json_within_limits(line)) {
                    response["ok"] = false;
                    response["error"] = QStringLiteral("request JSON exceeds limits");
                } else {
                    const QJsonDocument doc = QJsonDocument::fromJson(line, &parse_error);
                    if (parse_error.error != QJsonParseError::NoError || !doc.isObject()) {
                        response["ok"] = false;
                        response["error"] = QStringLiteral("invalid JSON");
                    } else {
                        QJsonObject request = doc.object();
                        const QJsonValue auth = request.take("auth");
                        const QByteArray supplied_secret = auth.isString()
                            ? auth.toString().toUtf8() : QByteArray();
                        if (!constant_time_equal(supplied_secret, expected_secret)) {
                            response["id"] = request.value("id");
                            response["ok"] = false;
                            response["error"] = QStringLiteral("unauthorized");
                        } else {
                            int nodes = 0;
                            if (!validate_shape(QJsonValue(request), 0, nodes)) {
                                response["id"] = request.value("id");
                                response["ok"] = false;
                                response["error"] = QStringLiteral("request JSON exceeds limits");
                            } else {
                                response = handle_request(request);
                            }
                        }
                    }
                }

                QByteArray encoded = QJsonDocument(response).toJson(QJsonDocument::Compact);
                // Include the line terminator in the wire-size bound.
                if (encoded.size() + 1 > kMaxResponseBytes) {
                    QJsonObject bounded;
                    bounded["id"] = response.value("id");
                    bounded["ok"] = false;
                    bounded["error"] = QStringLiteral("response exceeds size limit");
                    encoded = QJsonDocument(bounded).toJson(QJsonDocument::Compact);
                }
                if (!write_line(pipe, encoded)) break;
            }
        }

        if (connected) DisconnectNamedPipe(pipe);
        CloseHandle(pipe);
    }

    LocalFree(descriptor);
#endif
}

}  // namespace dolphin
