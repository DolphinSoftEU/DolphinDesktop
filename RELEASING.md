# Releasing DolphinDesktop

This guide describes the publication workflow in
[release.yml](.github/workflows/release.yml), the nonpublishing
[release-preflight.yml](.github/workflows/release-preflight.yml), and their
[validation scripts](.github/scripts/). Development setup is covered in
[CONTRIBUTING.md](CONTRIBUTING.md).

## Release contract

| Target | Trigger | Allowed source | Version |
| --- | --- | --- | --- |
| TestPyPI | Manual `Release` dispatch with `target=testpypi` | `testpypi-preview` or `master` branch | Source version; development versions receive a unique CI suffix |
| PyPI | Push of a matching final `vX.Y.Z` tag | Reviewed merge result reachable from `master` | Canonical final `X.Y.Z` only |
| Preflight | Manual `Release preflight` dispatch | Preview branches for `testpypi`; matching final tag for `pypi` | Same validation as the corresponding release path; never publishes |

Release consumes the distributions from the **latest push CI run for the exact
source SHA and branch**, and requires that run and its required jobs to pass.
It does not rebuild distributions or fall back to an older successful CI run.
PR CI alone does not qualify. Production dispatches and preview tag releases
are rejected by [validate_release.py](.github/scripts/validate_release.py).

The CI artifact contains one wheel, one sdist and a provenance manifest.
Release verifies its immutable artifact ID, archive digest, file hashes and
originating CI run/attempt. A clean Windows install/import/version/CLI smoke
must pass before either publishing job can run. Those jobs reverify the
distributions immediately before upload.

## Repository and index setup

Complete this setup before first use, and review it when policies change.
Workflow files do not configure hosted GitHub or package-index settings.

### Branches, tags and environments

Configure active branch rulesets with no bypass actors, required PRs, strict
required status checks, deletion protection and force-push protection:

| Branch | PR review policy |
| --- | --- |
| `testpypi-preview` | Zero required approvals; no last-push or code-owner approval requirement; the author may merge after checks pass |
| `master` | At least one independent approval; dismiss stale reviews and require last-push approval |

For both branches, configure every check name in `REQUIRED_JOBS` in
[release_provenance.py](.github/scripts/release_provenance.py), bound to GitHub
Actions integration `15368`. Keep CI's package-job dependencies intact: they
also gate packaging on minimum dependencies, runner/desktop acceptance, pytest
compatibility, native reproducibility and security checks.

Production tags matching `refs/tags/v*.*.*` require two active rulesets:

- Restrict tag updates and deletion, with no bypass actors.
- Restrict tag creation to dedicated releasers: a team, integration or repository
  administrator role. These actors must not bypass tag immutability or branch
  protection.

Configure environments with **Selected branches and tags**, using exactly:

| Environment | Allowed branches | Allowed tags | Approval |
| --- | --- | --- | --- |
| `testpypi` | `master`, `testpypi-preview` | None | No required reviewers, wait timer or custom manual deployment gate |
| `pypi` | None | `v*.*.*` | Required reviewers with self-review prevented |

Disable administrator bypass for both environments. Preserve any additional
production wait/custom protection. Only the publishing jobs receive
`id-token: write`; other release jobs use read-only permissions.

The policy audit reports `verified`, `unknown` or `invalid`. Read-only tokens
may hide `bypass_actors`; `unknown` permits the remaining checks to run but
does not verify authorization settings. Before initial acceptance, an
administrator must inspect the full bypass lists and tag creators, recording
the ruleset IDs, date and outcome outside this guide. Recheck after policy
changes. A visible policy violation is `invalid` and stops the workflow.

### Trusted Publishing

Configure a [Trusted Publisher](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)
separately on PyPI and TestPyPI with:

- Project: `dolphin-desktop`.
- Owner/repository: `DolphinSoftEU/DolphinDesktop`.
- Workflow filename: `release.yml`.
- Environment: `pypi` or `testpypi`, respectively.

Verify the index settings independently of GitHub. Remove obsolete publishers
and legacy upload tokens; publication uses GitHub OIDC.

### First deployment

Both dispatchable workflows must exist on the default branch, `master`, for
GitHub to accept standard manual dispatches. See the
[workflow_dispatch requirement](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch).
If either is absent, merge it and its matching script dependencies/tests through
normal review before attempting dispatch from `testpypi-preview`.

Check availability with GitHub CLI (`gh`, authenticated to this repository):

```powershell
gh workflow view release.yml --repo DolphinSoftEU/DolphinDesktop
gh workflow view release-preflight.yml --repo DolphinSoftEU/DolphinDesktop
```

## Prepare the source

Before merging a version PR:

1. Keep the version in `pyproject.toml` equal to `__version__` in
   `src/dolphin_desktop/__init__.py`. Update `uv.lock` with `uv lock` when source
   metadata changes, and run `uv lock --check`.
2. Update `CHANGELOG.md`. For a final release, give the release its version and
   intended publication date; keep future work under `[Unreleased]`.
3. Update README release-status text, installation examples and branch-specific
   badges/links when promoting a preview to a public release. Keep supported
   Python versions, extras and documentation consistent with the package.
4. Complete relevant checks from `CONTRIBUTING.md`, including native DLL rebuild
   and manifest verification when changing the Qt agent.

Production, and previews dispatched from `master`, require the exact source SHA
to be the merge result of a PR with an independent approval for its current
head. Reachability from `master` alone is insufficient. Do not tag an unreviewed
feature commit or select an arbitrary ancestor merely because its CI passed.

## Publish to TestPyPI

1. Merge the preview PR into protected `testpypi-preview` after checks pass.
   `master` is also supported, with its independent-review policy.
2. Wait for the latest **push CI** for that exact merge SHA to finish successfully,
   including its wheel installation checks. Record the CI run, attempt and
   distribution artifact.
3. Run nonpublishing preflight for the chosen branch:

   ```powershell
   gh workflow run release-preflight.yml --repo DolphinSoftEU/DolphinDesktop --ref testpypi-preview -f target=testpypi
   ```

   Inspect the completed result, including policy status and Windows wheel
   smoke. If the branch advances before dispatching Release, verify the new SHA
   and repeat preflight rather than relying on the previous run.
4. Dispatch publication from the same branch:

   ```powershell
   gh workflow run release.yml --repo DolphinSoftEU/DolphinDesktop --ref testpypi-preview -f target=testpypi
   ```

   Replace `testpypi-preview` with `master` in both commands when using master.
   No preview deployment approval is required.
5. Confirm that `Publish to TestPyPI` and `Smoke TestPyPI package` both pass.
   The downloaded wheel must match the CI wheel's SHA-256 before isolated
   installation/import/version/CLI checks run.

Source development versions `X.Y.Z.devN` are rewritten during CI to
`X.Y.Z.dev{CI_RUN_ID * 1000000 + CI_RUN_ATTEMPT}`. Release derives this version
from the originating CI attempt, not its own run. Final and RC version strings
are preserved; RCs can be previewed by branch dispatch, not by RC tag push.

## Publish to PyPI

1. Merge the final version PR into protected `master` with independent approval.
   Use canonical `X.Y.Z`; dev, RC, post, local and epoch versions cannot publish
   through the production path.
2. Wait for the latest master push CI for that exact merge SHA to pass. Record
   its artifact and verify production rulesets, environment and Trusted
   Publisher setup before creating the release tag.
3. Create and push the matching tag on that exact merge SHA. For example, for
   0.2.0, replace the SHA placeholder with the reviewed and CI-tested commit:

   ```powershell
   $releaseVersion = '0.2.0'
   $releaseSha = '<40-character reviewed merge SHA>'
   git fetch origin
   git tag -a "v$releaseVersion" $releaseSha -m "Release $releaseVersion"
   git push origin "refs/tags/v$releaseVersion"
   ```

   The tag push starts `Release`. A manual production dispatch is not supported.
   Existing release tags must not be moved or replaced.
4. Inspect policy/provenance validation and `Smoke exact wheel before publication`.
   An independent reviewer must approve the `pypi` environment before upload.
5. Confirm `Publish to PyPI` and `Smoke PyPI package` pass, including the
   downloaded wheel hash and isolated smoke. Create release notes after this
   verification and retain links to the CI and Release runs.

Production preflight can audit an **existing** matching tag with `target=pypi`.
It simulates the push event only inside the nonpublishing workflow. Pushing the
tag already starts Release, so this optional audit is not a pre-tag gate and
does not authorize a manual production dispatch. Release repeats its own checks.

## Failures and reruns

| Situation | Recovery |
| --- | --- |
| Latest source CI failed, is pending, or lacks required jobs/artifacts | Fix the cause and rerun CI. Prefer **Re-run all jobs** so the current attempt has a complete passing job set and a fresh package artifact. An older success/artifact is not a fallback. |
| CI artifact expired or is missing | Run CI again for the same reviewed source and wait for a new complete artifact; Release cannot rebuild it. |
| Release failed before any upload | Fix the failed prerequisite and rerun the failed Release jobs, provided the selected source CI/artifact is still valid. |
| Upload passed but the post-publication smoke failed | Inspect the index and logs. Rerun the failed smoke only if the rerun scope excludes the successful upload; otherwise verify the published wheel separately against the retained CI artifact. |
| Version collision or partially completed upload | Inspect which files reached the index. Do not overwrite them or enable `skip-existing`. Development previews need fresh CI to obtain a new version; RC/final releases need a new source version and, for production, a new matching tag. |

Use the GitHub Actions run page or
[GitHub CLI rerun controls](https://cli.github.com/manual/gh_run_rerun).
Check the included jobs before retrying: rerun controls may include dependencies.
Rerunning the whole Release after a successful upload attempts to upload the
same version again and can fail on the collision. Source CI attempts and
Release attempts have separate identities; verify which one failed first.

For a separate post-publication check, follow the download, hash comparison and
`smoke_wheel.py` steps in `smoke-testpypi` / `smoke-pypi` in `release.yml`, using
the recorded published version and original CI artifact. Release has no
verification-only dispatch mode.

Index download steps already retry propagation delays. If a version remains
unavailable, inspect the logged interpreter, version and index listing before
retrying. Both index smokes download Dolphin alone from one explicit index,
clear inherited pip configuration, and compare wheel hashes. Runtime
dependencies come separately from PyPI with lockfile hashes; the wheel is
installed with `--no-deps --no-index` in a clean environment outside the checkout.

## Validation and release records

Run the release-policy/provenance regression checks locally:

```powershell
uv run --locked --group dev pytest tests/framework/test_ci_security.py tests/framework/test_release_provenance.py tests/framework/test_release_preview.py -q
```

These checks exercise controlled success/failure cases. They do not certify
live GitHub permissions, index configuration or successful publication.

Record the source version/SHA, approved PR, source CI run/attempt, artifact ID
and digests, preflight status, publication run and downloaded wheel SHA-256.
Keep TestPyPI and PyPI records separate. Configuration audits and run-specific
findings belong in deployment/PR evidence, not as dated claims in this guide.
