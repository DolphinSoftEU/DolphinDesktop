# Qt Agent

dolphin_desktop ships an in-process **Qt Agent DLL** that extends Qt
support beyond what UIA can see — primarily Qt Quick / QML and
`QGraphicsView` custom-paint widgets that are opaque to UIA.

> - `dolphin_qt5_agent.dll` — built against Qt 5.15.2 + MSVC v142.
> - `dolphin_qt6_agent.dll` — built against Qt 6.11.1 + MSVC v143.
>
> Both DLLs ship with the Python wheel as prebuilt binaries — no
> separate install or build step.

## Why

The UIA path (with `QT_ACCESSIBILITY=1`) gives complete coverage of
`QWidget` controls. Two important categories remain unreachable:

1. **Qt Quick / QML** — the entire QML scene graph is rendered into a single
   `QQuickWindow` that exposes one `Pane` UIA node with no children, no
   matter how many controls are visible. This breaks any test of modern
   Qt 6 apps with `QtQuick.Controls 2`.
2. **`QGraphicsView` custom paint** — trading dashboards, node editors, and
   graph viewers draw with `QPainter` into a single canvas widget. UIA sees
   the `QWidget` host but nothing inside.

The established solution for this class of problem is **in-process
injection**: load a DLL into the AUT, walk Qt's `QObject` tree via the
meta-object system, and dispatch actions through
`QMetaObject::invokeMethod`. The Qt Agent does exactly that.

## Architecture

```
┌──────────────────────────────┐        named pipe         ┌─────────────────────┐
│ AUT process (Qt application) │ ◄──── \\.\pipe\dolphin... │ Test process        │
│                              │                            │ (dolphin_desktop)   │
│  ┌────────────────────────┐  │                            │                     │
│  │ dolphin_qt_agent.dll   │  │       JSON requests        │  QtAgentClient      │
│  │   - QObject walker     │  │  ────────────────────────► │   - connect()       │
│  │   - QMetaMethod invoke │  │       JSON responses       │   - tree()          │
│  │   - QQuickItem walker  │  │  ◄──────────────────────── │   - find_qml(...)   │
│  │   - paintEvent capture │  │                            │   - invoke(...)     │
│  └────────────────────────┘  │                            └─────────────────────┘
└──────────────────────────────┘
```

### Injection

We use `CreateRemoteThread + LoadLibraryW` (standard, well-documented
Windows pattern). The agent is built as a regular Qt-linked DLL that
exports a single C entry point:

```c
__declspec(dllexport) int dolphin_qt_agent_start(const char* pipe_name);
```

The injection helper is `src/dolphin_desktop/_qt_inject.py`. It calls
kernel32 through `ctypes` (`pywin32` is used only to enumerate the target's
module list), and asks for the five rights it actually needs rather than
`PROCESS_ALL_ACCESS`:

```python
hproc = OpenProcess(PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION
                    | PROCESS_VM_OPERATION | PROCESS_VM_WRITE | PROCESS_VM_READ,
                    False, pid)
_require_matching_arch(hproc, pid, dll_path)      # refuses on mismatch
addr = VirtualAllocEx(hproc, None, len(wide_path), MEM_COMMIT | MEM_RESERVE,
                      PAGE_READWRITE)
WriteProcessMemory(hproc, addr, wide_path)         # UTF-16, NUL-terminated
hthread = CreateRemoteThread(hproc, None, 0,
                             GetProcAddress(kernel32, "LoadLibraryW"), addr, 0)
WaitForSingleObject(hthread, 30_000)               # bounded, never INFINITE
```

After `LoadLibraryW` returns, we find the exported
`dolphin_qt_agent_start` and call it the same way, passing the pipe name.
Its address is `remote_module_base + rva`, where the RVA comes from parsing
the DLL's PE export table on disk — the agent links against Qt, so loading
it locally to resolve the export is not an option.

Two constraints follow from this design:

- **Architecture must match.** `LoadLibraryW` is resolved in *our own*
  kernel32 and that address is only valid in a process of the same
  architecture. `_inject_dll` compares the target process (via
  `IsWow64Process2`), the agent DLL's PE `machine` field and the host Python,
  and raises `QtAgentInjectError` when they differ instead of starting a
  remote thread at a foreign address (which terminates the target).
- **Remote allocations are leaked, not freed, once a remote thread has read
  them.** `VirtualFreeEx` while the remote thread is still running unmaps the
  argument it is reading and faults the AUT, so on a wait timeout the
  allocation is deliberately left behind. The pipe-name buffer is never freed
  at all — not even on success — because the agent's pipe server outlives a
  client disconnect (which is what `reattach()` relies on) and may re-read
  the pointer to re-create the pipe.

The client verifies the pipe's server process id
(`GetNamedPipeServerProcessId`) matches the injected pid before sending
anything, and holds an open handle to the target across
inject → start → connect — and again across `reattach()` — so the pid
cannot be recycled mid-sequence. The pipe is opened with
`SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION`: without it Windows
defaults named pipes to impersonation level, which would let a process
squatting the pipe name call `ImpersonateNamedPipeClient()` and act as the
test user before the pid check ever runs.

### IPC

A named pipe (`\\.\pipe\dolphin_qt_<pid>`) with line-delimited JSON:

```json
{"id": 1, "op": "tree", "root": "QMainWindow#main_window"}
{"id": 1, "ok": true, "result": [{"obj": "QPushButton", "name": "btn_ok", ...}]}

{"id": 2, "op": "invoke", "target": "QPushButton#btn_ok", "method": "click()"}
{"id": 2, "ok": true}

{"id": 3, "op": "qml_root"}
{"id": 3, "ok": true, "result": {"type": "ApplicationWindow", "children": [...]}}
```

Operations:

| Op | Returns |
|---|---|
| `tree` | Full `QObject` tree from `QApplication::topLevelWidgets()`. |
| `qml_root` | QML object tree from `QQmlApplicationEngine::rootObjects()`. |
| `find` | Match by `objectName`, `className`, `text`, regex on any. |
| `invoke` | Call any registered method via `QMetaObject::invokeMethod`. |
| `set_property` | Set any `Q_PROPERTY` value. |
| `get_property` | Read any `Q_PROPERTY` value. |
| `describe` / `members` | Full metadata / method + property listing for one handle. |
| `qml_find` / `qml_item_at` / `qml_click` | QML lookup, hit-test, synthetic click. |
| `graphics_items` / `graphics_item_at` | `QGraphicsScene` items, hit-test. |

Every reply echoes the request's `id`, and the client checks it — a
desynchronised stream would otherwise return the *previous* call's payload
for every later call. A reply carrying an id that was **never issued** is
treated as a genuine desync and marks the connection unusable; a reply to a
request that an earlier timeout abandoned is simply discarded and the read
continues. That forgiveness window holds the last 256 abandoned ids —
beyond it a late reply is indistinguishable from a desync and is treated as
one.

Replies are read with a bounded per-request timeout
(`QtAgentClient.rpc_timeout`, 30 s) and a 16 MiB size cap. Expiry raises
`QtAgentTimeoutError` and leaves the connection **usable** — a Qt event loop
blocked behind a native modal dialog is an ordinary, recoverable condition.
`QtAgentClient.reattach()` rebuilds the pipe (without re-injecting) when a
connection really has failed.

One `QtAgentClient` is safe to share across threads: `_send` holds a
reentrant lock across the whole write-then-read round trip. `close()` and
`reattach()` deliberately do **not** take that lock — they swap the handle
out under a separate short-lived lock and cancel pending I/O, so a watchdog
thread can preempt a `_send` parked on a write to a full pipe buffer
instead of queueing behind every outstanding request.

### Object identity

Stable handles use the `QObject*` pointer encoded as `0x{hex}` plus the
class name, e.g. `"QPushButton@0x7ff6a3b41ce0"`. The agent keeps a `QHash`
of valid handles and tracks `QObject::destroyed` to invalidate them.

## Python side

```python
from dolphin_desktop import Desktop

desktop = Desktop()
app = desktop.launch_qt("trading_app.exe")

# QML lookup (not reachable through UIA):
qml_chart = app.qml("PriceChart")
qml_chart.set_property("symbol", "AAPL")
qml_chart.invoke("zoom", 1.5)

# QGraphicsView item lookup (not reachable through UIA):
graph = app.graphics_view(object_name="market_depth")
item = graph.item_at(60, 40)
item.invoke("setVisible", True)
```

There is no `use_agent` flag: the agent attaches lazily on the first access
to `app.qt_agent` (which `app.qml()`, `app.qt_widget()` and
`app.graphics_view()` go through), and only for a process detected as Qt 5/6.
That first access:

1. Resolves the bundled `dolphin_qt{5,6}_agent.dll`.
2. Refuses if the target's architecture does not match the DLL and this Python.
3. Injects via `CreateRemoteThread` + `LoadLibraryW`.
4. Calls `dolphin_qt_agent_start` remotely with the pipe name.
5. Connects to the agent's named pipe (with retry loop) after checking the
   pipe's server pid.

`Application.has_qt_agent()` reports attachment without triggering injection.

UIA selectors continue to work unchanged — `Locator` falls back to UIA
when no agent is attached.

## Build

The agent DLLs are **prebuilt binaries bundled with the wheel**; their C++
source is not part of this repository. Both are `IMAGE_FILE_MACHINE_AMD64`
and export `dolphin_qt_agent_start` and `dolphin_qt_agent_stop`.

| File | Target |
|---|---|
| `dolphin_qt6_agent.dll` | Qt 6.11.x processes (e.g. PySide6) |
| `dolphin_qt5_agent.dll` | Qt 5.15.x processes (e.g. PyQt5 / older apps) |

They live in `src/dolphin_desktop/_qt_agent/` and ship with the wheel, so
end users need no build toolchain.

Anything that requires a DLL change — the pipe's security descriptor, or
whether `dolphin_qt_agent_start` copies its `pipe_name` argument — cannot
be verified or fixed from this repository.

## Limitations

- **x64 only** — both shipped DLLs are `IMAGE_FILE_MACHINE_AMD64`, and the
  injector needs the target, the DLL and the host Python to share one
  architecture. A 32-bit (or arm64) Qt app raises `QtAgentInjectError` and is
  never injected into; drive those apps with the UIA backend, which
  needs no agent. Adding 32-bit support means building an x86 agent *and*
  running the tests from an x86 Python.
- **No detach** — the DLL *does* export `dolphin_qt_agent_stop` alongside
  `dolphin_qt_agent_start`, but dolphin never calls it: nothing in this
  repository establishes when the agent's own threads are finished with the
  module, and a remote `FreeLibrary` that unmaps it under a live thread
  faults the AUT. So the agent stays loaded, with its thread and pipe, for
  the lifetime of the target process; `QtAgentClient.close()` closes only the
  client end. This matters for `Desktop.connect(pid=…)` against an app
  dolphin did not launch: the injection is permanent until that app exits.
  Use `QtAgentClient.reattach()` to rebuild a wedged connection instead.
- **Unauthenticated pipe** — `\\.\pipe\dolphin_qt_<pid>` has a predictable
  name and the agent's security descriptor is whatever the DLL sets. The
  client verifies the server's process id, so a squatter cannot impersonate
  the agent. See `SECURITY.md` for what this means for you in practice.
- **Per-Qt-major-version DLL** — Qt 5 and Qt 6 ABIs differ. We ship both,
  named `dolphin_qt5_agent.dll` and `dolphin_qt6_agent.dll`; the loader
  picks based on `Application.qt_version()`.
- **Anti-cheat / DRM** — apps that detect remote-thread injection (some
  games, banking apps) will reject the agent. There is no workaround
  short of cooperative integration.
- **Sandboxed apps** — UWP / packaged apps with isolated containers cannot
  be injected without elevated privileges and AppContainer-aware tooling.

## Python API

```python
from dolphin_desktop import Desktop

desktop = Desktop()
app = desktop.launch_qt(f'"{sys.executable}" "examples/qt_demo/qml_widgets.py"')

# Triggers DLL injection + named-pipe connect on first access.
agent = app.qt_agent

# 1. Pure introspection
roots = agent.qml_root()          # list of QQuickWindow trees
btns = agent.find(className="QPushButton")

# 2. Property access
nf = agent.qml_find("qmlNameField")[0]["handle"]
agent.set_property(nf, "text", "Alice")
assert agent.get_property(nf, "text") == "Alice"

# 3. Method invocation (no-arg / typed args)
spin = agent.find(className="QSpinBox")[0]["handle"]
agent.invoke(spin, "setValue", 42)

# 4. QML click simulation (Qt event system, not OS mouse)
btn = agent.qml_find("qmlClickButton")[0]["handle"]
agent.qml_click(btn)

# 5. QGraphicsView walking
view = agent.find(className="QGraphicsView")[0]["handle"]
items = agent.graphics_items(view)
hit = agent.graphics_item_at(view, 60, 40)
```

The agent is closed automatically when `Application.kill()` /
`Application.close()` is called. Use `app.has_qt_agent()` to check
attachment without triggering injection.
