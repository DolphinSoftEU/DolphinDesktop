# Releasing DolphinDesktop

Release publishes the exact distributions from the latest successful **push CI**
for the selected SHA and branch. It never rebuilds a package or falls back to an
older successful run. GitHub enforces branch, tag and environment policies;
artifact provenance is validated separately. No OpenSSL, signed ruleset JSON,
private signing key or expiring administrative evidence is used (KAN-741).

## Native GitHub configuration

Configure active rulesets with no bypass actors, required PRs, strict required
GitHub Actions checks, deletion protection and force-push protection:

| Source | Review policy | Required checks |
| --- | --- | --- |
| `testpypi-preview` | PR required; **zero approvals**, no last-push approval or code-owner review requirement; author may merge after checks | Every name in `REQUIRED_JOBS` in `.github/scripts/release_provenance.py`, bound to GitHub Actions integration 15368 |
| `master` | At least one independent approval, dismiss stale reviews, require last-push approval | The same required checks |

CI also gates package creation on the minimum dependency matrix (all Python
versions and profiles), CVE audit, SAST and secret scans. Keep these dependencies.

Production tags `refs/tags/v*.*.*` need two active rulesets: update/deletion
restrictions without bypass actors, and a creation restriction with dedicated
releasers (team, integration, or repository administrator role). Do not grant
those releasers bypass on tag immutability or branch protection.

Configure environments using **Selected branches and tags**, with exactly:

| Environment | Allowed branches | Allowed tags | Manual approval |
| --- | --- | --- | --- |
| `testpypi` | `master`, `testpypi-preview` | None | None; remove required reviewers |
| `pypi` | None | `v*.*.*` | Required reviewers, prevent self-review, administrator bypass disabled |

Disable administrator bypass for both environments. Preserve any required wait
or custom production protection; preview must have no manual deployment gate.
Only the publishing jobs receive `id-token: write`. Other jobs have read-only
permissions, and all Actions dependencies are pinned to immutable SHAs.

Read-only GitHub tokens can omit `bypass_actors`. Preflight checks active rules,
required checks and visible bypass actors, but does **not** claim that omitted
actors prove an empty bypass list. GitHub remains responsible for native policy
enforcement. Administrators must keep bypass lists empty as specified above;
there is no recurring manual attestation. See the
[GitHub rules API](https://docs.github.com/en/rest/repos/rules#get-a-repository-ruleset).

Configure a Trusted Publisher in each package index: project `dolphin-desktop`,
owner `DolphinSoftEU`, repository `DolphinDesktop`, workflow `release.yml`, and
matching environment `testpypi` or `pypi`. Revoke obsolete publishers and legacy
upload tokens. An administrator must verify the index configuration independently
of GitHub; environment names cannot prove index settings.

## Nonpublishing preflight

Run **Release preflight** (`release-preflight.yml`) on `testpypi-preview` or
`master` with target `testpypi`. For production, select the existing matching
`vX.Y.Z` tag and target `pypi`; dry-run simulates the tag-push event only inside
this workflow. It has no publishing action, environment deployment or OIDC
permission and cannot authorize a production dispatch in `release.yml`.

Preflight checks the ref and source version, Git ancestry, native policies, the
latest exact-SHA push CI and its successful required jobs, attempt-specific
artifact ID, expiry and archive digest. It downloads that artifact, verifies
its distribution manifest and CI identity, derives its expected version and
runs an isolated Windows wheel install/import/CLI smoke. Rejection explains the
failed gate without printing credentials or signed evidence.

## TestPyPI checklist

1. Merge the version/code PR into protected `testpypi-preview` after checks pass.
   Its author can merge it; a second account is unnecessary. `master` remains
   supported for preview, with its stricter review policy.
2. Wait for the **push** CI for that exact merge SHA to finish successfully.
3. Run **Release preflight** for the same branch and inspect the result.
4. Dispatch **Release** from the same branch with `testpypi`. Tags, arbitrary
   branches and arbitrary SHAs are rejected for preview. No deployment approval
   is required. Trusted Publishing uses OIDC, never long-lived upload tokens.
5. Verify the post-publish smoke: the downloaded TestPyPI wheel must have the
   same SHA-256 as the CI wheel, then pass install/import/version/CLI checks.

Development versions `X.Y.Z.devN` become
`X.Y.Z.dev{CI_RUN_ID * 1000000 + CI_RUN_ATTEMPT}` during CI. Release derives this
version from the selected CI run/attempt, rather than its own run identity.
Releasing the same artifact twice fails on the index version collision:
`skip-existing` is never enabled. Rerun CI to obtain a fresh development version.
RC versions may be previewed by branch dispatch; RC tag pushes are not supported.

## PyPI checklist

1. Merge matching `pyproject.toml`, package `__version__` and changelog changes
   through independent review into protected `master`. Use canonical final
   `X.Y.Z`; development, RC, post, local and epoch versions are rejected.
2. Wait for the exact-SHA master push CI to pass. Create the matching immutable
   `vX.Y.Z` tag on a commit reachable from master and push it.
3. Release accepts production **only on tag push**. A manual production
   `workflow_dispatch` is always rejected, even on a matching existing tag.
   A failed production run can be rerun using GitHub's run-rerun facility.
4. Inspect the manifest, exact CI run/attempt and local Windows wheel smoke.
   An independent reviewer must approve the native `pypi` environment; self-review
   and admin bypass are disabled. No production upload occurs without this gate.
5. Verify the downloaded PyPI wheel hash against the CI artifact and its isolated
   smoke result. Create release notes after successful verification.

Both upload jobs require the local wheel smoke. They download by immutable
artifact ID and reverify the anchored manifest hash and both distribution hashes
immediately before upload. Missing/expired artifacts require another CI build.
Partial CI reruns must rebuild the package in the selected attempt.

Both post-publish smokes download DolphinDesktop with `--no-deps` from one explicit
index, clearing inherited pip configuration. Dependencies come separately from
PyPI with lockfile hashes. They install the downloaded wheel with
`--no-deps --no-index` in a clean venv, outside the source checkout. A same-version
wheel with different bytes is rejected before installation.

## Validation and deployment evidence

Run:

```powershell
uv run --locked --group dev pytest tests/framework/test_ci_security.py tests/framework/test_release_provenance.py tests/framework/test_release_preview.py -q
```

Record hosted preflight failures for an unauthorized ref, a missing/pending CI
and invalid digest using controlled tests; then record successful branch preflight,
a complete TestPyPI Release and the downloaded wheel SHA-256. Keep preview and
production evidence separate. Do not change KAN-579's status before re-auditing
its original unauthorized-release risk. Local tests cannot certify hosted GitHub
or index configuration.

Read-only inspection on 2026-10-20 found preview requiring one approval and last
push approval, TestPyPI requiring manual environment review, and production
missing the required master/tag/environment protection. These settings must be
migrated before the corresponding hosted positive tests; code changes alone do
not update GitHub settings. Remove the obsolete repository variable
`RELEASE_RULESETS_ATTESTATION` during migration. Keep private keys outside the
repository; they are no longer used by Release.
