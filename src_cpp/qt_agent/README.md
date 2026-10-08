# Qt agent native source

This directory contains the C++ implementation of the in-process Qt agent.
The agent exposes QObject, QML and QGraphicsView inspection over a named pipe.
The Python-side protocol and lifecycle are documented in
[`docs/architecture/qt-agent.md`](../../docs/architecture/qt-agent.md).

## Reproducible Windows builds

Build from a clean, committed checkout in an x64 MSVC developer shell. The
scripts pin the Qt and MSVC toolchain versions, use a fresh Ninja build
directory, verify the authenticated start/stop exports, and update the DLL's
SHA-256, size, protocol version and source commit in
`src/dolphin_desktop/_qt_agent/agent_manifest.json`.

Install CMake 3.30+, Ninja, and the matching Qt SDK with Core, Widgets,
Network, Qml and Quick modules. Then set the SDK paths and run:

```powershell
$env:DOLPHIN_QT5_ROOT = 'C:\Qt\5.15.2\msvc2019_64'
$env:DOLPHIN_QT6_ROOT = 'C:\Qt\6.11.1\msvc2022_64'
.\src_cpp\qt_agent\build_qt5.ps1
.\src_cpp\qt_agent\build_qt6.ps1
```

Qt 5.15.2 requires MSVC 19.29 (14.29.30133); Qt 6.11.1 requires MSVC 19.44
(14.44.35207). The build scripts reject different versions so the manifest
cannot silently attribute an artifact to an unrecorded toolchain.

The generated DLLs are written to `src/dolphin_desktop/_qt_agent/`. Review
the DLLs and manifest together; the manifest's `source_commit` identifies the
committed C++ source used for each binary. The DLLs currently checked into
this branch are legacy builds; the Python client refuses them until both are
rebuilt with the v2 exports.

## IPC security

The v2 server creates a named pipe with a protected DACL for the target
process's logon SID and `PIPE_REJECT_REMOTE_CLIENTS`. It checks the connecting
client PID and a separate per-attach secret before dispatching any operation.
Request bytes, JSON depth and node count, response bytes, connection idle time
and pipe writes are bounded in the native server.

The stop export requests shutdown and joins the pipe thread. It deliberately
does not unload the DLL from under Qt callbacks; the module remains mapped
until the target process exits.
