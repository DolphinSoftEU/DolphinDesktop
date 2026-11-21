// Named-pipe JSON server for the dolphin Qt agent.
#pragma once

#include <QString>
#include <atomic>

namespace dolphin {

class PipeServer
{
public:
    PipeServer(QString pipe_name, QString session_secret, quint32 client_pid);
    ~PipeServer();

    // Serves reconnects until the controller process exits or stop() is called.
    void run();
    void stop();

private:
    QString pipe_name_;
    QString session_secret_;
    quint32 client_pid_;
    std::atomic<bool> stop_{false};
#ifdef _WIN32
    // Keep the process object pinned, not just its recyclable numeric PID.
    void* client_process_ = nullptr;
    void* client_wait_ = nullptr;
#endif
};

}  // namespace dolphin
