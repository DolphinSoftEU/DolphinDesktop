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

    // Blocks until the client disconnects or stop() is called.
    void run();
    void stop();

private:
    QString pipe_name_;
    QString session_secret_;
    quint32 client_pid_;
    std::atomic<bool> stop_{false};
};

}  // namespace dolphin
