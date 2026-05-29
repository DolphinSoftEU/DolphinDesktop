# CI Setup

Dolphin tests need Windows. The most reliable CI setup is an agent running as a logged-in user with access to an interactive desktop.

## Recommended Baseline

Start with visible mode:

```bash
pip install dolphin-desktop pytest
pytest tests/ -v --dolphin-backend=uia
```

Use `dolphin-run` only after you have verified that your target application and test actions work on a hidden Windows desktop:

```bash
dolphin-run pytest tests/smoke/ -v --dolphin-backend=uia
```

## Agent Requirements

| Requirement | Reason |
| --- | --- |
| Windows Server 2019/2022 or Windows 10/11 | Dolphin depends on Windows UIA, Win32, and COM |
| Python 3.11+ | Package requirement |
| Agent runs as a normal logged-in user | UIA cannot drive another user's desktop |
| Same privilege level as the app under test | Non-elevated tests cannot automate elevated apps |
| Artifact upload for traces/screenshots/videos | Desktop failures are easier to debug visually |

Avoid running desktop UI tests as `SYSTEM` unless you have a dedicated interactive-session setup and have verified UIA access with `dolphin doctor`.

## Artifacts

Useful options:

```bash
pytest tests/ -v --dolphin-screenshot-on-fail --dolphin-trace=on-failure
pytest tests/ -v --dolphin-video=keepfailedonly
```

Upload these directories when they exist:

- `dolphin-traces/`
- `dolphin-videos/`
- `dolphin-screenshots/`
- `dolphin-report.html`

## CI Guides

- [GitHub Actions](github-actions.md)
- [Jenkins](jenkins.md)
- [TeamCity](teamcity.md)

See [Headless Mode](../tutorials/headless-mode.md) before using `dolphin-run` in a full suite.
