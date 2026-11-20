# Security Policy

## Reporting a Vulnerability

Report suspected vulnerabilities privately to
[kontakt@dolphinsoft.pl](mailto:kontakt@dolphinsoft.pl). Do not publish exploit
details or sensitive diagnostics in a public issue or pull request.

Include the affected Dolphin/Python/Windows versions, application/backend,
expected impact, and a minimal reproducer or proof of concept where possible.
Use test data and remove credentials, personal information and production
content from attachments.

We aim to acknowledge reports within **5 business days**. After triage, we
coordinate fixes and disclosure timing with the reporter; this is not a fixed
deadline for resolving every report.

## Supported Versions

Security fixes are prioritized for the latest public release line. Reports
concerning the current development branch and TestPyPI previews are also
welcome; previews are testing builds. Older release lines do not receive
guaranteed backports.

The behavior below describes the **0.2.0 line**. Earlier releases may lack these
protections; see [CHANGELOG.md](CHANGELOG.md) for release status and changes.

## Application and data access

Dolphin controls applications with the permissions of the test process. Use
isolated test accounts, profiles and data. Connecting to an existing application
can expose its open documents, sessions and authenticated content.

### Artifacts and redaction

Screenshots, videos, UI trees, traces, crash dumps and generated recordings can
contain sensitive data, including content from other windows or monitors.
Text redaction does not inspect image pixels. Review artifacts before sharing,
restrict access and retain only what is needed. Disable unnecessary collection
with `--dolphin-trace=off` and `--dolphin-video=off`; failure screenshots are
enabled separately with `--dolphin-screenshot-on-fail`.

Dolphin masks recognized sensitive keys and supported sensitive parameter
values in its managed logs/reports, including pytest failure text, traces,
self-healing diagnostics, Allure result metadata and captured streams, and
JUnit XML. This remains best-effort, not a guarantee that arbitrary data is safe
to publish. Unlabelled values, multiline secrets, credentials inside URLs and
third-party output may escape pattern-based masking.

Wrap a sensitive value in `Secret(value)` before passing it to a supported
action or diagnostic. `str()` and `repr()` of the wrapper hide its value.
Automatic literal masking uses a bounded process-local registry of the most
recent 512 values, and registers only values of at least four characters. It
does not guarantee literal masking of shorter values or values already evicted.
Sensitive pytest parameter values have separate per-test handling; values of
1–3 characters are masked as standalone tokens, not arbitrary substrings.

The following remain caller-owned:

- Bodies/files passed directly to `allure.attach()` or `allure.attach.file()`;
  `Secret` registration does not intercept those calls.
- Direct output that bypasses pytest capture or terminal reporting, including
  writes with capture disabled (`-s`).
- Pytest's local `.pytest_cache`, which retains original test IDs.

Publish JUnit XML only after pytest finishes: sanitization runs at session end,
and a killed process can leave the original XML unprocessed. See the
[tracing guide](docs/tutorials/tracing.md) for artifact controls.

### Recorder

The recorder installs desktop-wide mouse/keyboard hooks. Choose `--app` or
`--pid` to filter recorded actions to one process; capture across applications
requires explicit `--global` (`global_recording=True` in Python).

Password and unclassified controls are masked. Plaintext typing is recorded
only for stable native Windows Edit controls classified as ordinary text.
A secret entered into such a control can still appear in the generated Python
file. Review generated code before committing or sharing it, and stop recording
before entering real credentials.

### Chromium remote debugging

CDP debugging endpoints provide privileged access to page data, cookies and
script execution. Dolphin's launch helpers connect through loopback and verify
listener ownership; they do not add endpoint authentication, configure the
target's network binding or isolate its user profile.

Use disposable test profiles, restrict access to debugging endpoints, and close
the application when testing ends. A unique port helps avoid collisions but
does not provide access control. Do not assume that every Electron/CEF host
binds only to loopback.

### Qt agent and native libraries

The Qt agent loads a bundled DLL into the target process and can inspect
objects, write properties and invoke methods. Use it only with applications
you are authorized to control. Agent DLLs are hash-checked before injection;
authenticated per-attach IPC restricts the controller and rejects remote pipe
clients. These checks do not sandbox the application. Only x64 Qt targets are
supported.

Java Access Bridge requires an explicitly configured absolute `JAVA_HOME`;
HLLAPI requires an absolute `hllapi_dll_path`. Use trusted runtimes and DLLs:
an absolute path alone does not make a library trustworthy. See the
[Java](docs/guides/java.md) and [mainframe](docs/guides/mainframe.md) guides.

### Mainframe connections

For native TN5250 and s3270, `tls=True` selects certificate/host-name-verified
TLS without silent fallback to plaintext. Remote plaintext requires explicit
`allow_plaintext=True`; port 992 alone does not enable TLS. Loopback plaintext
is allowed, so secure any tunnel independently. HLLAPI transport security is
controlled by the terminal emulator's session configuration.

### Office documents

`ExcelApp.open()`, `WordApp.open()` and `PowerPointApp.open()` disable VBA by
default; `allow_macros=True` explicitly permits it subject to Office policy.
Excel/Word also suppress automatic external-link updates during opening.
These wrappers are not document sandboxes: Excel 4.0/XLM macros, linked content
and other active content require appropriate Office controls. `connect()` does
not change security settings for documents already open. See the
[Office guide](docs/guides/office.md).

### Optional telemetry

Telemetry is off by default. It requires the `telemetry` extra,
`DOLPHIN_TELEMETRY=on` and a configured Sentry DSN (`SENTRY_DSN`). When enabled,
Dolphin exception diagnostics can leave the machine for that Sentry service.
Local-variable collection and default Sentry integrations are disabled, and
non-Dolphin source context is removed from exception frames. Review the
telemetry configuration before enabling it with sensitive test data.
