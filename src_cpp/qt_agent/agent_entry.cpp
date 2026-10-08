// Entry points for the authenticated Qt agent.

#include <QApplication>
#include <QJsonDocument>
#include <QJsonObject>
#include <QString>

#include <atomic>
#include <mutex>
#include <thread>

#include "pipe_server.h"

#ifdef _WIN32
#include <windows.h>
#endif

namespace {

std::mutex g_lifecycle_mutex;
// These handles are deliberately process-lifetime allocations. If the AUT
// exits without an explicit stop call, C++ global destructors must not destroy
// a joinable std::thread while the OS is tearing down the process.
dolphin::PipeServer* g_server = nullptr;
std::thread* g_server_thread = nullptr;
std::atomic<bool> g_server_running{false};
bool g_started = false;
QString g_pipe_name;
QString g_session_secret;
quint32 g_client_pid = 0;

void agent_main(dolphin::PipeServer* server)
{
    server->run();
    g_server_running = false;
}

bool parse_start_config(const char* raw, QString& pipe_name,
                        QString& session_secret, quint32& client_pid)
{
    if (!raw) return false;
    const QByteArray bytes(raw);
    if (bytes.isEmpty() || bytes.size() > 4096) return false;

    QJsonParseError error;
    const QJsonDocument doc = QJsonDocument::fromJson(bytes, &error);
    if (error.error != QJsonParseError::NoError || !doc.isObject()) return false;
    const QJsonObject config = doc.object();
    pipe_name = config.value("pipe_name").toString();
    session_secret = config.value("session_secret").toString();
    const double raw_pid = config.value("client_pid").toDouble();
    if (pipe_name.isEmpty() || session_secret.size() < 32 ||
        session_secret.size() > 64 || raw_pid < 1 || raw_pid > 0xFFFFFFFFu ||
        raw_pid != static_cast<double>(static_cast<quint32>(raw_pid))) {
        return false;
    }
    if (!pipe_name.startsWith(QStringLiteral("\\\\.\\pipe\\dolphin_qt_"))) return false;
    for (const QChar ch : session_secret) {
        const bool allowed = (ch >= QLatin1Char('a') && ch <= QLatin1Char('z')) ||
            (ch >= QLatin1Char('A') && ch <= QLatin1Char('Z')) ||
            (ch >= QLatin1Char('0') && ch <= QLatin1Char('9')) ||
            ch == QLatin1Char('-') || ch == QLatin1Char('_');
        if (!allowed) return false;
    }
    client_pid = static_cast<quint32>(raw_pid);
    return true;
}

}  // namespace

extern "C" {

#ifdef _WIN32
#define DOLPHIN_EXPORT __declspec(dllexport)
#else
#define DOLPHIN_EXPORT __attribute__((visibility("default")))
#endif

// Called through a remote thread. The JSON argument contains the random pipe
// name, a separate per-attach secret and the only process permitted to connect.
DOLPHIN_EXPORT int dolphin_qt_agent_start_v2(const char* raw_config)
{
    QString pipe_name;
    QString session_secret;
    quint32 client_pid = 0;
    if (!parse_start_config(raw_config, pipe_name, session_secret, client_pid)) return 1;
    if (!qApp) return 2;

    std::lock_guard<std::mutex> lock(g_lifecycle_mutex);
    if (g_started && g_server_running) {
        return g_pipe_name == pipe_name && g_session_secret == session_secret &&
               g_client_pid == client_pid ? 0 : 3;
    }

    if (!g_server_thread) {
        try {
            g_server_thread = new std::thread();
        } catch (...) {
            return 5;
        }
    }

    // Reap a server thread that exited after a pipe creation failure before
    // replacing the server object it referenced.
    if (g_server_thread->joinable()) {
        if (g_server_thread->get_id() == std::this_thread::get_id()) return 4;
        g_server_thread->join();
    }
    delete g_server;
    g_server = nullptr;

    try {
        g_server = new dolphin::PipeServer(pipe_name, session_secret, client_pid);
        g_pipe_name = pipe_name;
        g_session_secret = session_secret;
        g_client_pid = client_pid;
        g_server_running = true;
        g_started = true;
        *g_server_thread = std::thread(agent_main, g_server);
    } catch (...) {
        g_server_running = false;
        g_started = false;
        delete g_server;
        g_server = nullptr;
        return 5;
    }
    return 0;
}

// Stop and join the pipe thread before returning. The DLL is deliberately
// left mapped in the AUT; callers must not FreeLibrary while any agent code can
// still be running.
DOLPHIN_EXPORT int dolphin_qt_agent_stop_v2()
{
    std::lock_guard<std::mutex> lock(g_lifecycle_mutex);
    if (g_server) g_server->stop();
    if (g_server_thread && g_server_thread->joinable()) {
        if (g_server_thread->get_id() == std::this_thread::get_id()) return 1;
        g_server_thread->join();
    }
    delete g_server;
    g_server = nullptr;
    g_server_running = false;
    g_started = false;
    g_pipe_name.clear();
    g_session_secret.clear();
    g_client_pid = 0;
    return 0;
}

#ifdef _WIN32
BOOL APIENTRY DllMain(HMODULE /*hModule*/, DWORD /*reason*/, LPVOID /*reserved*/)
{
    // Do not join threads or call Qt from DllMain. The library stays mapped for
    // the process lifetime; the explicit stop entry point owns server shutdown.
    return TRUE;
}
#endif

}  // extern "C"
