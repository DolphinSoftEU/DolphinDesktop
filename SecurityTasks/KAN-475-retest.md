# KAN-475 — completing the CDP retest

The 2026-10-05 Jira comment identified an incomplete retest: mocked handshakes
passed, but Playwright was missing and real Chromium profile isolation was not
verified. The referenced `tests/zt_14_uia` test is absent from this checkout;
its real-browser regression now lives in
`tests/component/test_cdp_endpoint_identity.py`.

Run on Windows from the repository root:

```powershell
uv sync --locked --group dev --extra vision --extra cdp
uv run --no-sync python -m playwright install chromium
uv run --no-sync pytest tests/component/test_cdp_endpoint_identity.py -q --junitxml=artifacts/kan-475-cdp.xml
uv run --no-sync pytest tests/framework/test_cdp_fixture.py -q
uv run --no-sync pytest tests/framework/test_desktop.py tests/framework/test_security_core.py tests/framework/test_cdp_session.py tests/framework/test_pytest_plugin.py tests/framework/test_ci_security.py -q
```

The component test launches Chromium through `Desktop.launch_electron_cdp` and
uses the real Windows listener lookup and Playwright handshake, with no mocked
ownership checks or CDP connection. Two workers use distinct ephemeral ports
and temporary profiles, serving different pages from the same local origin.
The test checks that each nonempty listener owner set belongs to the launched
PID or its verified descendants, plus page identity and cookie/localStorage isolation,
and continued operation of the second worker after the first closes. It then
reuses the first port for a foreign HTTP 200 server and verifies launch refusal,
before successfully connecting a new Chromium session with an empty profile.
Both debugger listeners must disappear after cleanup.

Fixture regressions also cover delayed startup: a timeout returns without
waiting for the worker, and queued cleanup closes any late session and kills
its application on the owning thread. Errors after acquiring a session use
the same cleanup path.

Missing Playwright/Chromium fails the test. The Windows component CI job installs
the locked CDP extra and Chromium and checks that this scenario exists in its
JUnit results and passes without a skip. The pytest plugin also preserves the
tuple representation of skipped reports while redacting their contents, fixing
the `_folded_skips` assertion mentioned in the retest comment.
