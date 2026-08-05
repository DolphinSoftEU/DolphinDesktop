# Security Policy

## Supported Versions

Security fixes are provided for the latest public release line.

| Version | Supported |
| ------- | --------- |
| 0.2.x   | Yes       |
| 0.1.x   | No        |

## Security-relevant behavior you should know about

`dolphin-desktop` drives other applications, so by design it does things a
normal library does not. Three of them open a local attack surface while a
test session runs, and all three are under your control.

**Chromium remote debugging.** `launch_electron_cdp()` appends
`--remote-debugging-port` (default `9222`; `launch_cef_cdp()` uses `8080`)
to the application's command line, and the port stays open for that
process's lifetime. Chromium binds it to `127.0.0.1` only, so it is not
reachable from the network — but the CDP protocol has **no
authentication at all**, so any process running as the same user can
execute arbitrary JavaScript in the automated application, read its DOM,
cookies and local storage, and — if the app enables Node integration —
run arbitrary code as that user. The default port is the well-known
Chromium one, so it is also guessable and can collide with other tooling.
Mitigations: pass a unique high `debug_port` per run, automate only
test or throwaway profiles rather than an app signed in to real
accounts, avoid shared and multi-user machines, and make sure the
application is terminated when the test finishes.

**Qt agent DLL injection.** `QtAgentClient.attach(pid, ...)` loads a
bundled DLL into the target process with `CreateRemoteThread` and talks to
it over a named pipe (`\\.\pipe\dolphin_qt_<pid>`). The pipe name is
predictable and the server accepts whichever local process connects
first; anyone able to open it gets full `QObject` introspection, property
writes and `QMetaObject` invocation in the target. dolphin's own client
verifies the pipe server's owning process, so a squatter cannot forge
responses back to your test, and it connects at
`SECURITY_IDENTIFICATION` so a squatting server cannot impersonate the
(possibly elevated) account running the tests. Neither of those protects
the application itself. Attach only to applications you launched or own. Only 64-bit Qt
targets are supported; injection into a 32-bit process is refused rather
than attempted.

**The recorder captures all keyboard input.** `dolphin record` installs a
low-level keyboard hook. Without an application filter it records every
keystroke on the machine — including passwords typed into unrelated
applications — as plain text in the generated script. Run it with a filter,
and review generated scripts before committing them.

Failure artifacts (screenshots, videos, crash dumps) capture the whole
desktop, not just the application under test. Treat them as sensitive
before attaching them to a public issue tracker or CI artifact store.

Traces additionally store the failing test's pytest report, which under
`pytest -l` (`--showlocals`) contains the values of locals in the failing
frame. That text is passed through the same redaction as logging: a value
assigned to a name containing `password`, `passwd`, `passphrase`, `pwd`,
`secret`, `token`, `api_key`, `private_key`, `credential`, `authorization`,
`auth`, `signature`, `sessionid` or `sas` is masked, including inside a
compound name such as `AWS_SECRET_ACCESS_KEY`, and including the credential
after any HTTP auth scheme.

Redaction is pattern-based and therefore best-effort. It looks for a
credential *assigned* to a recognised name, so the following are **not**
covered — this list is illustrative, not exhaustive:

- a secret held in a differently named variable;
- a value with no `:` or `=` between it and the name — a `.netrc` line, an
  `argv` list printed by a `subprocess` error, `--password hunter2`, an HTML
  `<input type="password" value="…">` captured in a DOM snapshot, an AS/400
  signon screen scrape;
- a value spanning lines: a PEM or OpenSSH key block, a YAML block scalar,
  a triple-quoted string;
- an unquoted value containing a semicolon (`PWD=my;pass`), masked only up
  to the semicolon, because there the semicolon is the connection-string
  delimiter;
- a URL with inline credentials (`https://user:pass@host`).

Treat the filter as a safety net, not a control. Do not publish traces,
Allure reports or the self-healing journal from a run that had real
credentials in scope without reading them first; `--dolphin-trace=off`
disables trace capture entirely, and the surest option remains not putting a
production credential in a test run.

## Reporting a Vulnerability

Please report suspected vulnerabilities privately by emailing kontakt@dolphinsoft.pl.
Do not open a public issue for security-sensitive reports.

Include:

- A description of the issue and expected impact.
- Reproduction steps or proof-of-concept code, if available.
- Affected versions and operating system details.

We aim to acknowledge reports within 5 business days and will coordinate fixes
and disclosure timing with the reporter.
