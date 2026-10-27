# Qt agent native source

This directory contains the C++ implementation of the in-process Qt agent.
The agent exposes QObject, QML and QGraphicsView inspection over a named pipe.
The Python-side protocol and lifecycle are documented in
[`docs/architecture/qt-agent.md`](../../docs/architecture/qt-agent.md).

## Reproducible Windows builds

Stage the intended source, then build from an x64 MSVC developer shell with
no unstaged changes under `src_cpp/qt_agent`. The scripts pin the Qt and MSVC
toolchain versions, use a fresh Ninja build directory, verify the authenticated
start/stop exports, and update the DLL's SHA-256, size, protocol version and
staged Git tree in
`src/dolphin_desktop/_qt_agent/agent_manifest.json`.

Use PowerShell 7, MSVC tools 14.50.35717 (compiler 19.50.35726), Windows SDK
10.0.26100.0, and the matching Qt SDK with Core, Widgets, Network, Qml and
Quick modules. QML is required, so incomplete SDKs fail instead of producing a
different artifact. Install the locked CMake 3.31.10, Ninja 1.11.1.4 (PyPI)
and aqtinstall 3.3.0 tools with `uv sync --locked --group native-build`.
Initialize `vcvars64.bat 10.0.26100.0 -vcvars_ver=14.50.35717`, then run
the following in PowerShell 7 with `.venv/Scripts` first on PATH:

```powershell
$env:DOLPHIN_QT5_ROOT = 'C:\Qt\5.15.2\msvc2019_64'
$env:DOLPHIN_QT6_ROOT = 'C:\Qt\6.10.3\msvc2022_64'
.\src_cpp\qt_agent\build_qt5.ps1
.\src_cpp\qt_agent\build_qt6.ps1
```

Both SDKs use MSVC compiler 19.50.35726 in this build environment. Qt 5.15.2
is installed as `msvc2019_64`; Qt 6.10.3 is installed as `msvc2022_64`. The
build scripts reject different compiler and Qt versions so the manifest
cannot silently attribute an artifact to an unrecorded toolchain.

The generated DLLs are written to `src/dolphin_desktop/_qt_agent/`. Review
the DLLs and manifest together; the manifest's `source_tree` identifies the
staged source snapshot used for each binary. The checked-in DLLs are protocol
v2 builds, and Python verifies their manifest size and SHA-256 before
injection.

### Verify shipped artifacts

`build_qt5.ps1 -Verify` and `build_qt6.ps1 -Verify` build into a fresh
temporary directory, check exports, and compare the result against both the
existing DLL and every recorded manifest field. They never replace the DLL
or regenerate its manifest. A mismatched hash, source tree or toolchain fails.
Build directories are removed in `finally`, including on verification or
compiler failure. CMake/build output and errors remain in
`artifacts/qt-agent-builds/` (or the shared builder's `-LogDirectory`).

From the same developer environment, run:

```powershell
.github/scripts/verify_qt_agents.ps1
```

This archives HEAD into two clean checkouts with different path lengths,
including spaces, and relocates the Qt SDK through directory junctions. It
verifies both ABIs in each location and retains logs in
`artifacts/qt-agent-reproducibility/`, including build diagnostics in `build/`.
Both temporary checkouts and SDK junctions are removed on success or failure;
the installed SDK targets are preserved. Before committing a change, stage the
source, rebuilt DLLs and manifest and use `-Revision (git write-tree)` to test
that exact snapshot without creating a commit.

`/Brepro` alone did not reproduce DLLs across checkout locations. The compiler
also needs `/experimental:deterministic` and `/pathmap` for source, generated
source and Qt SDK paths, including paths expanded by Qt headers. The linker
uses a fixed PDB name and disables incremental linking. The scripts reject
environment flag overrides and record the compiler tools, Windows SDK,
CMake and Ninja versions alongside each binary's hash and source tree.

CI installs these pinned inputs, runs the same verification in two locations,
and uploads logs. This job gates package creation and release provenance;
merely updating a manifest cannot make a different shipped DLL pass.

## IPC security

The v2 server creates a named pipe with a protected DACL for the target
process's logon SID and `PIPE_REJECT_REMOTE_CLIENTS`. It checks the connecting
client PID and a separate per-attach secret before dispatching any operation.
Request bytes, JSON depth and node count, response bytes, connection idle time
and pipe writes are bounded in the native server.
An oversized request receives `code: request_too_large` and the wire-size
`limit` (including the newline), without parsing or dispatching its JSON.
The rejected connection drains input using a fixed buffer for at most five
seconds while the client reads the error and closes; it cannot dispatch further
RPC. Disconnect, timeout and other RPC errors are not accepted as evidence of
size rejection by the boundary regression.

The stop export requests shutdown and joins the pipe thread. It deliberately
does not unload the DLL from under Qt callbacks; the module remains mapped
until the target process exits.
