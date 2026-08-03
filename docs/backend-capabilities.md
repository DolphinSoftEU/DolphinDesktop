# Backend capabilities

`dolphin_desktop` is a **multi-stack** automation library — one API
that talks to native Windows apps, SAP GUI, Qt, Electron, Java
Swing, Delphi, and 3270 / 5250 terminals. Each stack has different
mechanics (UIA patterns for native, COM Scripting for SAP, CDP for
Electron, JAB for Java, direct socket protocol for mainframe). Not
every operation is possible on every stack — a mainframe terminal
does not implement `.invoke()` because there is no InvokePattern in
3270; an image-matching locator cannot walk an accessibility tree
because there is no tree to walk.

**Capabilities** are the vocabulary the library uses to answer the
question "does the backend I selected support this operation?"
BEFORE the test runs and BEFORE the operation fails opaquely at
runtime.

## The `Capability` enum

Every operation the library exposes is one enum member in
`dolphin_desktop.Capability`. Adding a new capability is a public
API change and shows up in every backend's declared set.

| Group | Members |
|---|---|
| Discovery | `LOCATE`, `GET_TREE` |
| State reads | `READ_TEXT`, `READ_STATE` |
| Input simulation (needs input desktop) | `CLICK`, `DOUBLE_CLICK`, `RIGHT_CLICK`, `HOVER`, `DRAG`, `TYPE_TEXT`, `PRESS_KEY`, `SCROLL` |
| Programmatic actions (headless-safe) | `INVOKE`, `TOGGLE`, `EXPAND`, `COLLAPSE`, `SELECT`, `SET_VALUE` |
| Media | `SCREENSHOT` |

The Programmatic group pairs with the programmatic action API: its
members are what dolphin's `.invoke()` / `.toggle()` / `.set_value()`
route through under headless. If your backend declares `Capability.INVOKE`, users
can call `.invoke()`; if it does not, they get a clean
`UnsupportedCapabilityError` naming the backend and pointing at
alternatives that do support it.

### Preset sets

Three ready-made `frozenset`s cover the shapes backends actually declare,
so a new backend rarely has to enumerate members by hand:

| Preset | Contains |
|---|---|
| `ALL_CAPABILITIES` | all 19 members — a fully-featured accessibility backend |
| `STANDARD_ACCESSIBILITY` | `ALL_CAPABILITIES` minus `DRAG`, `SCROLL` and `SCREENSHOT` — an accessibility API that can read and act on the tree but has no pixel or gesture support |
| `IMAGE_ONLY` | what template matching alone can do: `LOCATE`, `CLICK`, `DOUBLE_CLICK`, `RIGHT_CLICK`, `HOVER`, `TYPE_TEXT`, `PRESS_KEY`, `SCREENSHOT` — no tree, no state reads, no programmatic actions |

```python
from dolphin_desktop import IMAGE_ONLY, STANDARD_ACCESSIBILITY, Capability

assert Capability.INVOKE in STANDARD_ACCESSIBILITY
assert Capability.INVOKE not in IMAGE_ONLY   # no accessibility tree to invoke through
```

## Built-in capability matrix

| Backend | LOCATE | GET_TREE | CLICK | TYPE_TEXT | INVOKE | TOGGLE | SELECT | SET_VALUE | SCREENSHOT |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| `uia` (UIABackend) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `qt` (QtBackend) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `delphi` (DelphiBackend, marker) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `cdp` (CDPBackend, marker) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `sap` (SapBackend, marker) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `java` (JavaBackend, marker) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `win32` (Win32Backend) | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| `mainframe` (MainframeBackend, marker) | ✅ | ❌ | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| `image` (ImageBackend) | ✅ | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| `macos`, `linux` (stubs) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

Full matrix (all 19 capabilities) available via
`dolphin_desktop.list_backends()` — every entry carries a
`capabilities` list.

## Querying capabilities from user code

**Backend-level**:

```python
from dolphin_desktop import Capability, resolve_backend

b = resolve_backend("uia")
assert b.supports(Capability.INVOKE)
```

**Desktop-level** — the currently-configured backend:

```python
from dolphin_desktop import Desktop, Capability

desktop = Desktop(backend="image")
if not desktop.backend_supports(Capability.INVOKE):
    pytest.skip("image backend does not implement invoke()")
```

**Stack-facade-level** — every facade class publishes `backend_id`:

```python
from dolphin_desktop import CDPSession, Capability

if CDPSession.backend_supports(Capability.SET_VALUE):
    session.locator("#login").set_value("alice")
```

**Cross-registry query** — "which backends can do X?":

```python
from dolphin_desktop import Capability, supported_backends

for bid in supported_backends(Capability.INVOKE):
    print(bid, "supports .invoke()")
# → uia, qt, delphi, cdp, sap, java
```

## Failure surface — `UnsupportedCapabilityError`

Every operation gated on a capability check raises
`UnsupportedCapabilityError` when the backend does not declare
support. The exception carries:

- The backend id
- The missing capability name
- A `hint=` pointing at working alternatives from the registry

Example message:

```
UnsupportedCapabilityError: backend 'image' does not support Capability.INVOKE
  hint: other backends that support this: cdp, delphi, java, qt, sap, uia
        — pass one via Desktop(backend='…')
```

`UnsupportedCapabilityError` is distinct from
`UnsupportedPatternError`:

- `UnsupportedCapabilityError`: the **backend as a whole** does not
  publish the operation — e.g. calling `.invoke()` on
  `Desktop(backend="image")`.
- `UnsupportedPatternError`: the backend publishes the operation but
  the **specific element** does not implement it — e.g. a JLabel is
  not toggleable even though the JAB backend supports toggle in
  general.

Both derive from `dolphin_desktop.DolphinError` so tests can catch
either with the base class.

## Writing a plugin backend

Third-party packages register additional backends via the
`dolphin_desktop.backends` entry-point group. Example — a fictional
Bloomberg Terminal automation stack:

```python
# my_plugin/bloomberg_backend.py

from dolphin_desktop import Backend, Capability


class BloombergBackend(Backend):
    """Automate a Bloomberg Terminal window through its SPI API."""

    id = "bloomberg"
    platform = "windows"

    def capabilities(self):
        # Bloomberg SPI supports: locate widgets, read text, type
        # commands, press function keys, take screenshots. No
        # programmatic patterns or accessibility tree.
        return frozenset({
            Capability.LOCATE,
            Capability.READ_TEXT,
            Capability.TYPE_TEXT,
            Capability.PRESS_KEY,
            Capability.SCREENSHOT,
        })

    def find_element(self, parent, criteria):
        self.require_capability(Capability.LOCATE)
        # …call Bloomberg SPI…

    def type_text(self, element, text):
        self.require_capability(Capability.TYPE_TEXT)
        # …dispatch through SPI…

    def click(self, element, *, button="left"):
        self.require_capability(Capability.CLICK)  # raises — not in caps

    # …etc for the other five abstract methods.
```

Register via `pyproject.toml`:

```toml
[project.entry-points."dolphin_desktop.backends"]
bloomberg = "my_plugin.bloomberg_backend:BloombergBackend"
```

Users of your plugin then get automatic integration:

```python
from dolphin_desktop import Desktop, Capability

desktop = Desktop(backend="bloomberg")
desktop.require_capability(Capability.TYPE_TEXT)  # passes
desktop.require_capability(Capability.INVOKE)     # raises with hint
```

## Design principles

1. **Safe by default.** `Backend.capabilities()` default returns an
   empty set. A subclass that overrides nothing gets clean
   `UnsupportedCapabilityError` on every operation instead of silent
   pass-through to a broken parent implementation.

2. **Capabilities are boolean, not tri-state.** Either the backend
   supports the operation across every reachable element or it does
   not. Per-element partial support (a JLabel is not toggleable even
   though JAB supports toggle) is signalled through
   `UnsupportedPatternError`, not by omitting the
   capability here.

3. **The vocabulary is stable.** Enum members are semver-visible.
   Adding a new capability is a minor version bump; removing one is
   a breaking change.

4. **Marker backends declare stack capabilities.** `DelphiBackend`,
   `CDPBackend`, `MainframeBackend`, `SapBackend`, `JavaBackend` are
   registry markers whose real automation surface lives on the
   matching facade (DelphiApp, CDPSession, etc.). They still publish
   `capabilities()` because callers ask
   `CDPSession.backend_supports(...)` and the facade forwards to the
   backend registry — the marker must return the right answer.
