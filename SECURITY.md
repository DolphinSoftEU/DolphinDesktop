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
it over a per-attach named pipe. The authenticated v2 server restricts its
DACL to the target process's logon SID, sets `PIPE_REJECT_REMOTE_CLIENTS`,
checks the connecting controller PID, and requires a separate random secret
on every RPC. The Python client verifies the pipe server's process id and
refuses a reused target PID. The DLL source and pinned Qt/MSVC build scripts
are in `src_cpp/qt_agent/`; the scripts record each rebuilt DLL's hash, size
and source commit in the required manifest. `QtAgentClient.close()` requests
an orderly server stop and join; the DLL remains mapped until the target exits.
Only 64-bit Qt targets are supported; injection into a 32-bit process is
refused rather than attempted.

The agent provides powerful access: `QObject` introspection, property writes
and `QMetaObject` invocation. Attach only to applications you launched or
own. Releases must rebuild both native DLLs from the staged native source and
review their manifest entries before packaging them. Legacy DLLs without the
v2 start export are refused before injection. The checked-in DLLs use protocol
v2 and have their source tree, build toolchain, size and SHA-256 recorded in
the manifest.

**Mainframe transport.** Terminal sessions carry the sign-on credentials
in the same byte stream as the screen, and neither EBCDIC nor Telnet
provides confidentiality. `Desktop.mainframe()` therefore refuses a
plaintext connection to a non-loopback host unless you pass
`allow_plaintext=True`. Pass `tls=True` for a verified TLS channel
(certificate chain and host name are checked; there is no fallback to
plaintext); a port number, 992 included, does not enable TLS on its own.
Wrap a password in `Secret(...)` before typing it so it is masked in every
log, trace, crash dump and report. The native TN5250 backend also bounds
how much unterminated data it will buffer while looking for a record
boundary, and enforces its read deadline unconditionally — a host that
never terminates a record (or streams them continuously) cannot grow the
client's buffer without bound or keep a call blocked past its own timeout.

**The recorder captures all keyboard input.** `dolphin record` installs a
low-level keyboard hook. Without an application filter it records every
keystroke on the machine — including passwords typed into unrelated
applications — as plain text in the generated script. Run it with a filter,
and review generated scripts before committing them.

Failure artifacts (screenshots, videos, crash dumps) capture the whole
desktop, not just the application under test. Text redaction cannot inspect
image pixels, so screenshots and videos can still show passwords, PINs, or
other sensitive values. Treat them as sensitive before attaching them to a
public issue tracker or CI artifact store. Restrict access to the people who
need the artifacts, use the shortest practical CI retention period, and remove
local copies after investigation. The CI example uses seven days as a starting
point; set a shorter period when your policy requires it. Disable collection
that is not needed with `--dolphin-trace=off` and `--dolphin-video=off`; the
separate `--dolphin-screenshot-on-fail` capture is opt-in.

Traces additionally store the failing test's pytest report, which under
`pytest -l` (`--showlocals`) contains the values of locals in the failing
frame. That text is passed through the same redaction as logging: a value
assigned to a name containing `password`, `passwd`, `passphrase`, `pwd`,
`secret`, `token`, `api_key`, `private_key`, `credential`, `authorization`,
`auth`, `signature`, `sessionid`, `sas`, `pin`, `login`, `username`, `user_id`,
`connection_string` or `clipboard` is masked, including inside a compound
name such as `AWS_SECRET_ACCESS_KEY`, including camelCase PIN names such as
`pinCode` and `cardPin`, including the credential after any HTTP auth scheme,
and including a value shown inside a Python dict `repr()`
(e.g. `{'login': '…'}`, the shape an unresolved locator's criteria are
rendered in). The same redaction runs at every artifact boundary — trace
steps, crash-dump ZIPs, the Allure stdout/stderr attachments, the
self-healing journal (`selfheal.jsonl`) and UIA tree dumps — and, for
structured data such as a selector's criteria dict, a sensitive *key* masks
its whole value whatever its type.

With Dolphin's pytest plugin enabled, the optional Allure integration also
redacts completed result/container models before any reporter serializes them.
This covers `statusDetails.message` and `statusDetails.trace` for tests, fixture
setup/teardown and nested steps, including diagnostics that Allure builds
directly from an exception instead of pytest's report. Parameters and labels
named by sensitive keys such as `pin` have their entire value masked. Test
statuses, IDs and attachment references retain their original meaning.

Values of sensitive Allure parameters are also masked when repeated in display
names, including pytest's default parameter IDs and custom `ids=` strings.
Sensitive pytest parameters are snapshotted before fixture setup. Their scalar
leaves are also detected below sensitive dictionary keys inside otherwise public
parameters, such as `payload={"pin": value, "operation": "public"}`. Only the
sensitive subtree is collected; public siblings remain readable. These
leaves, including nested dictionary/list values, also mask captured stdout and
stderr when printed without a key or `Secret` wrapper. The same values protect
Allure display names when custom IDs extract a leaf such as `pin["code"]`.
This context belongs to the individual test; it does not add values to the
global registry used by `Secret`.
Sensitive values supplied through `allure.dynamic.parameter()` extend the
current test's context, including structured values that are later replaced
with a display mask in Allure metadata.
Supported leaves include strings, bytes, numbers, `None`, `pathlib.PurePath`
(including `Path`), `uuid.UUID`, `Decimal` and `Enum` (its display, name and
payload), also inside standard containers. Arbitrary objects are not converted
through user-defined `str`/`repr`; expose a supported scalar representation
and use non-sensitive `ids=` for such objects.

For short parameter values (1–3 characters), automatic masking applies to
standalone tokens, using Unicode word boundaries. Thus `o` is masked in
`Authenticating with o`, while `operation` and unrelated numbers remain readable.
Longer values are masked even inside other text. Do not concatenate short
secrets into larger words/identifiers: sanitize those strings explicitly, use
non-sensitive `ids=`, or register a `Secret` for unconditional literal masking.

With `--junitxml`, Dolphin sanitizes the completed XML at session finish,
after pytest's writer runs and before the pytest process returns. Each testcase
is matched to its own sensitive values using a temporary opaque property,
removed from the finished XML. A parameter in one test does not mask another
test's name or diagnostics. Test names, diagnostics, captured streams and custom
attribute values are masked, also when using xdist. Suite-level metadata uses
ordinary text redaction without applying individual tests' parameter values.
Sensitive `property` names (for example `password`) mask the entire `value`
attribute, including `record_property()` and suite-level properties, without
requiring prior `Secret` registration. Testcase properties also protect repeated
values inside that same testcase.
Verdicts, counters and timings are preserved. Publish the file only after pytest exits;
an interrupted/killed process may leave the original writer's XML unprocessed.
HTML fallback rows and SQLite trace identifiers use the same per-test masking.
Trace text fields retain their owning test's parameter context, including actions
recorded on another thread. Trace directories and screenshot/video filenames use
opaque random names, independent of parameter values.

Pytest's terminal writer masks recognized sensitive identifiers in verbose test
output and collection listings. With xdist, start lines are deferred until the
completed report supplies the worker's sensitive-value snapshot. This changes
display text; execution/collection IDs and report identities remain unchanged.
Pytest's local `.pytest_cache` retains original IDs for selection and `--lf`;
do not publish it as a report. Prefer safe `ids=` when sharing execution metadata.
Direct writes with capture disabled (`-s`) and third-party output that bypasses
pytest's terminal writer remain caller-owned.

**Native Allure attachments are caller-owned.** The automatic protection above
covers report models and captured stdout/stderr, not arbitrary bodies passed
directly to `allure.attach()` or files passed to `allure.attach.file()`.
Registering a value with `Secret` does not intercept these SDK calls. Sanitize
the text or file before passing it to Allure; for example, format a `Secret`
into the text (`f"pin={Secret(pin)}"`) rather than attaching its revealed value.

The surest control is not the pattern but `Secret`: wrap a value in
`dolphin_desktop.Secret("…")` before handing it to `type_text` (UIA, CDP
and mainframe locators accept it). The action receives the real characters;
the literal value is then masked wherever it appears in Dolphin-managed artifact text,
independent of the pattern and of any variable name. `str(secret)` and
`repr(secret)` never reveal it.

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

## GitHub Actions and release gates (KAN-474)

The product repository is `DolphinSoftEU/DolphinDesktop`. Its tracked
`.github/workflows/`, `pyproject.toml` and `uv.lock` are the inputs for retesting
KAN-474; the separate `dolphin-desktop.ta` repository cannot establish these
acceptance criteria.

| Workflow / jobs | Events | Token permissions |
| --- | --- | --- |
| CI: unit, component, package, security | PR and push to master/testpypi-preview | `contents: read` |
| Docs: build | PR and push for docs, source, dependency or workflow changes | `contents: read` |
| Docs: deploy | Push to master only, after build succeeds | `contents: write` for mike's gh-pages commits |
| Release: security, smoke | Version tag push or manual dispatch | `contents: read` |
| Release: prepare CI distributions | After release security checks | `contents: read`, `actions: read`, `pull-requests: read` |
| Release: publish-testpypi, publish-pypi | After security and build succeed, selected target only | `id-token: write`, `contents: read`, protected target environment |

All external Actions use full commit SHAs. Dependabot proposes reviewed updates
in `.github/dependabot.yml`. Read-only checkouts do not persist repository
credentials. PRs run on hosted runners using `pull_request`, without a write
token or OIDC. Only the master documentation deploy persists a token to push
gh-pages. Production validation allows only a final `X.Y.Z` version from
a matching final tag whose commit is already in `origin/master`.
TestPyPI allows `master`, `testpypi-preview` and matching final/RC tags. Full
checkout history supports the production ancestry check; validation fails
before build outputs are written if the source, target, ref or version is
invalid. Configure matching environment rules and independent approval as
described in [RELEASING.md](RELEASING.md); these settings are outside the repository.

uv itself is pinned to 0.11.9. Dependency installation and export use `--locked`,
which rejects stale project metadata without rewriting the lock. Security and
build tools (including the hatchling backend), docs and Qt test bindings come
from `uv.lock`. Package builds use `--no-isolation` with the locked backend,
so build does not resolve a new backend in a separate environment. `--no-sync`
commands use the environment installed by the immediately preceding locked
sync; normal CI commands use `--locked`.

Both CI and release run the following checks on Windows and Linux to evaluate
platform-specific dependency markers:

- pip-audit checks the locked dependencies, all extras and dependency groups;
  any known vulnerability or strict audit failure blocks the job.
- Bandit checks the shipped Python source; medium and high severity findings
  block the job (`-ll`).
- detect-secrets checks tracked files against the reviewed `.secrets.baseline`;
  new findings block the job. Baseline changes require review of each finding,
  rather than automatic regeneration to make the gate pass.

Release's build job depends on the complete security matrix. Both publishing
jobs depend on build and download only that run's `dolphin-desktop-dist-attempt-N`
artifact. Security and build check out the default revision of the same run;
publication cannot reuse a successful check from a different commit. Package
smoke tests intentionally install from the public index to validate distribution
behavior, rather than replacing the user's dependency resolution with the CI lock.

Run the configuration regressions from the product repository:

```powershell
uv lock --check
uv run --locked pytest tests/framework/test_ci_security.py -q
```

These cover event triggers, effective job permissions, action SHA pins, release
gate dependencies and a deliberately stale lockfile rejected by real uv. A full
retest additionally needs GitHub Actions run URLs and commit SHAs for a PR, a
branch push and a release/dispatch run. Verify that a failing security matrix
skips build and publication, and that a successful approved run publishes the
artifact from that same run. Local configuration tests do not establish hosted
runner, environment approval or Trusted Publisher behavior.

### Retest observations, 2026-10-11

The GitHub environment API was checked for both `pypi` and `testpypi`.
Both require approval by `DolphinSoftEU`, but `prevent_self_review` is false
and `deployment_branch_policy` is null: allowed deployment branches/tags are
unrestricted. Approval therefore does not enforce a separate reviewer or an
approved release ref. Before treating these environments as protected release
boundaries, restrict the allowed refs and enable prevention of self-review
according to the maintainer's release policy.

The follow-up attempted to enable selected branches/tags and Prevent
self-review in both environments, preserving their current required reviewer.
Both environment update requests were rejected with HTTP 403 (`Must have admin
rights to Repository`), so those settings remain unchanged. Administrator
action is required; the exact desired settings and the implications of the
single existing reviewer are documented in [RELEASING.md](RELEASING.md).

Local follow-up validation: 47 CI policy tests passed, including rejected
manual production deployments from preview/feature branches, non-final versions,
wrong tag versions, and real Git repositories with unmerged tagged commits.
The workflow validates the full `github.ref`, fetches history for the
ancestry check, and enforces the additional KAN-579 gates below. This does not replace GitHub Environment rules: an unapproved
ref can contain a modified workflow or validator. Production deployment remains
dependent on administrator configuration of the server-side restrictions.

The latest available [release run](https://github.com/DolphinSoftEU/DolphinDesktop/actions/runs/37151851015)
used commit `71fe568a06d09575c37f8f87b150e2600b3891c4` and had no separate
security matrix. It does not prove that the new `build → security` dependency
blocks publication on failure. A hosted run of the updated workflow with a
failing security check remains required; both build and publishing jobs must
be skipped. Do not approve an actual publication as part of that negative test.

The local lock regression now creates a minimal project with a local path
dependency and an isolated cache. It first creates and checks a valid lock,
then changes dependencies and requires the specific `uv lock --check` stale
lock diagnostic without rewriting the file. Updating and checking the lock
again must succeed offline, ruling out unavailable registry packages as the
cause of the negative result. The CI policy regressions also reject mutated
workflows with ignored job/step failures, shell failure masking, removed job
dependencies or `always()` conditions around the release gate.

Windows Qt tests constrain `PyQt5-Qt5` to 5.15.2 because newer releases may
lack Windows wheels. The lock includes a Windows x64 wheel; the regression
checks its presence, and the six authenticated Qt5/Qt6 agent integration
scenarios passed after installation from the updated lock on Windows with
Python 3.12 and 3.13. All 19 CI policy tests passed on Windows with Python
3.11, 3.12 and 3.13; Ruff lint/format and mypy also passed.

Broader local checks did not establish a completely passing suite:

- `pytest tests/qt -q --maxfail=1`: 61 passed, then
  `test_qt_core.py::test_get_by_object_name_finds_widget` failed to locate the
  Qt6 widget `qt_btn_ok` through UIA. The same failure reproduced in the
  existing `.venv` with the unchanged PySide6 6.10.3 version.
- `pytest -m unit -n 4 -q --maxfail=1`: 2150 passed, one skipped, then
  `TestReportCollection::test_reports_with_failures_are_serializable_under_xdist`
  failed because its nested pytest run encountered `FileNotFoundError`.
  Running that test alone passed in both the new and existing environments;
  the failure under concurrent execution remains unexplained.

## Protected-source publication (KAN-579)

Release now consumes the exact distributions produced and smoke-tested on
Windows by a completed push CI run for the release SHA; it does not rebuild.
It verifies required jobs in the latest run attempt, protected branch rules and
independent PR approval, immutable tags, and environment reviewer/ref/bypass
settings before downloading the artifact. Archive SHA256, manifest identity and
both distribution hashes must match. Publishing checks those file hashes and
the anchored manifest again after environment approval. Production branch
dispatches are rejected, including `master`.

The 2026-10-13 GitHub inspection still found insufficient server-side protection.
The available push credential cannot administer this repository; an attempted
`pypi` environment update returned HTTP 403. Full acceptance requires an
administrator to configure rulesets/environments and verify Trusted Publishers
on both indexes, then record hosted positive/negative run evidence. See
[RELEASING.md](RELEASING.md) for the exact configuration and release procedure.
The new validator blocks publication under the observed settings. Local mocked
API and artifact tests establish rejection behavior, not completed deployment
configuration or a successful hosted release.

Read-only GitHub ruleset responses can hide `bypass_actors`. An omitted/null
field is rejected unless signed administrative evidence matches the current
policy and update timestamp. Evidence has a maximum 24-hour lifetime, is bound
to this repository, and is verified against a public key committed through
review to the protected source. The independent workstation audit alone receives
an administrator credential; the release `GITHUB_TOKEN` remains read-only.
Tag tests model separate creation and immutability rulesets. CI and release
artifact names include their run attempt; selection never falls back to a
package built in an earlier attempt. See the administrative attestation procedure
in [RELEASING.md](RELEASING.md).

## Security regression mapping

The table below maps each Jira item to its corresponding regression test; it
is not a claim that every item is closed by this change. The issue-to-test
pairs use the actual security scope of each item. Adjacent hardening that is
not one of these items is kept in the codebase but is not claimed as coverage
for them. The tests are intentionally kept next to the affected code so a
future refactor cannot preserve a prose claim while dropping the regression:

| Work item | Regression | Test location | Status / invariant covered |
| --- | --- | --- | --- |
| KAN-467 | DESKTOP-199 | `tests/framework/test_mainframe.py::test_s3270_rejects_unsafe_host_before_spawn_or_stdin` | s3270 host input cannot inject another emulator action; the process is not spawned. |
| KAN-468 | DESKTOP-200 | `tests/framework/test_security_mainframe.py::test_tn5250_tls_handshake_verifies_the_certificate`; DESKTOP-200 TLS UAT | Verified TLS is enforced for native TN5250 and the s3270 `L:` path when explicitly requested; remote plaintext requires `allow_plaintext=True`. |
| KAN-469 | KAN-535 / DESKTOP-201 | `tests/framework/test_security_core.py::test_trace_db_never_stores_a_secret`, `::test_crash_dump_zip_never_stores_a_secret`, `::test_allure_text_attachment_is_redacted`; `tests/framework/test_allure_redaction.py`; `tests/framework/test_security_mainframe.py::test_s3270_trace_names_the_action_not_the_typed_text`, `::test_hllapi_payload_functions_are_not_dumped` | Textual PINs and marked secrets are redacted in real Allure result/container JSON, steps, parameters and captured attachments, as well as trace/crash artifacts and mainframe diagnostic logs. |
| KAN-472 | DESKTOP-203 | `tests/framework/test_security_core.py::test_terminate_skips_when_identity_was_never_recorded`, `::test_terminate_skips_when_current_identity_cannot_be_read` | Cleanup does not terminate a PID unless its recorded creation time can be confirmed. |
| KAN-473 | DESKTOP-204 | `tests/framework/test_java.py::test_session_is_singleton_and_init_uses_only_trusted_absolute_dll_path`; `tests/framework/test_mainframe.py::test_resolve_hllapi_dll_success_and_failure` | JAB and HLLAPI DLL loading use explicit trusted paths; relative and DLL search-order/PATH fallback is refused. |
| KAN-475 | DESKTOP-205 | `tests/framework/test_desktop.py::test_cdp_rejects_http_endpoint_owned_by_foreign_pid`; `tests/framework/test_security_core.py::test_verify_cdp_port_owner_rejects_unknown_owner` | A live CDP endpoint is accepted only when its listener PID is known and belongs to the launched process tree. |
| KAN-574 | — | `tests/framework/test_office_com.py::test_excel_open_force_disables_macros_and_link_updates`, `::test_word_open_force_disables_macros` | Opening a document through `ExcelApp.open()` / `WordApp.open()` force-disables macros before the file is opened. |
| KAN-575 | — | `tests/framework/test_mainframe.py::test_tn5250_read_records_rejects_unbounded_backlog_without_eor`, `::test_tn5250_read_records_enforces_deadline_even_with_records_already_parsed` | A TN5250 host that never terminates a record cannot grow the receive buffer without bound, and the read deadline is enforced even once records have started arriving. |
| KAN-630 | DESKTOP-107 | `tests/framework/test_diagnostics_privacy.py::TestRedactionShapesThatLeakedBefore::test_the_value_never_survives`, `tests/framework/test_selfheal.py::test_selfheal_redacts_a_selector_keyed_by_a_sensitive_name_with_a_bare_value` | `login`/`username`/`connection_string`/`clipboard` are masked in a dict-`repr()` shape, and a selector dict keyed by a sensitive name with a bare value is masked before it reaches the on-disk self-healing journal. |
| KAN-636 | — | `tests/framework/test_cli_init.py::test_init_rejects_absolute_path_outside_cwd`, `::test_init_rejects_path_traversal`, `::test_init_rejects_symlinked_target_escaping_cwd` | `dolphin init` refuses a target that resolves outside the current working directory. |

KAN-468 uses explicit transport selection. Native TN5250 and s3270 `L:`
connections verify their configured certificate policy before application
data is sent. A remote plaintext connection, including on port 23, requires
`allow_plaintext=True`; a loopback connection is exempt.

Run the focused set with:

```powershell
uv run pytest `
  tests/framework/test_mainframe.py `
  tests/framework/test_stability.py `
  tests/framework/test_java.py `
  tests/framework/test_desktop.py `
  tests/framework/test_office_com.py `
  tests/framework/test_diagnostics_privacy.py `
  tests/framework/test_selfheal.py `
  tests/framework/test_cli_init.py -q
```

## Reporting a Vulnerability

Please report suspected vulnerabilities privately by emailing kontakt@dolphinsoft.pl.
Do not open a public issue for security-sensitive reports.

Include:

- A description of the issue and expected impact.
- Reproduction steps or proof-of-concept code, if available.
- Affected versions and operating system details.

We aim to acknowledge reports within 5 business days and will coordinate fixes
and disclosure timing with the reporter.
