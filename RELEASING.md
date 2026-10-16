# Releasing dolphin-desktop

The release workflow publishes the exact wheel and sdist already built and
smoke-tested by CI on Windows. It never rebuilds distributions. Production
requires a matching final `vX.Y.Z` tag; manual publication from `master`, preview
or feature branches is rejected. TestPyPI permits `master`, `testpypi-preview`,
and matching final/RC tags (`vX.Y.Z-rc.N` for `X.Y.ZrcN`). Tags use a reviewed
merge commit on `master`; preview branch dispatches use its reviewed merge commit.

## Administrator setup (KAN-579)

Configure active GitHub rulesets for both `master` and `testpypi-preview`:

- Require a pull request and at least one independent approval, dismiss stale
  approvals, and require approval of the most recent reviewable push.
- Require all checks below, with GitHub Actions as the expected source and
  strict up-to-date checks. Disable force pushes and deletion.
- Leave the bypass list empty, including administrator bypasses.

Required check names (must match CI job names):

- `Unit / Python 3.11 / Windows`
- `Unit / Python 3.12 / Windows`
- `Unit / Python 3.13 / Windows`
- `Windows component tests`
- `Linux package install and import`
- `Build package`
- `Security gate (CVE / secrets / SAST) / ubuntu-latest`
- `Security gate (CVE / secrets / SAST) / windows-latest`

Create an active tag ruleset matching `refs/tags/v*.*.*` with update and deletion
restrictions and an empty bypass list. A separate creation ruleset must restrict
release tagging to a designated team/integration or repository administrators;
use `always` bypass mode for those designated creators, and do not give them
bypass rights on the update/deletion ruleset. A creation restriction with no
allowed creator is rejected because it would prevent every release tag.
The release validator checks the effective branch
rules, tag immutability, and an independent approval for the exact merged PR
head whose merge result is the release SHA. Classic branch protection alone is
not accepted by this validator.

Configure both [GitHub Environments](https://github.com/DolphinSoftEU/DolphinDesktop/settings/environments)
with required reviewers, **Prevent self-review**, **Allow administrators to
bypass configured protection rules** disabled, and **Selected branches and tags**:

| Environment | Branch rules | Tag rules |
| --- | --- | --- |
| `pypi` | None | `v*.*.*` |
| `testpypi` | `master`, `testpypi-preview` | `v*.*.*` |

Remove all broader rules. The validator requires these exact rules and rejects
missing protection. With only `DolphinSoftEU` as reviewer, another collaborator
must initiate the run or a second trusted reviewer must be appointed.

In each package index, configure a Trusted Publisher for project
`dolphin-desktop`, owner `DolphinSoftEU`, repository `DolphinDesktop`, workflow
`release.yml`, and the corresponding environment (`pypi` or `testpypi`). Remove
obsolete publishers and revoke legacy upload tokens that could bypass this
workflow. An index administrator must record dated evidence of these settings;
GitHub environment names alone do not prove Trusted Publisher configuration.
See [PyPI Trusted Publisher configuration](https://docs.pypi.org/trusted-publishers/adding-a-publisher/)
and [GitHub environment configuration](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

The read-only inspection on 2026-10-13 confirmed that `master` only has a deletion
rule, `testpypi-preview` is unprotected, no active tag ruleset exists, and both
package environments allow self-review/admin bypass and have no ref restrictions.
The available credential has push access but `admin=false`. Applying the `pypi`
environment hardening returned HTTP 403, `Must have admin rights to Repository`.
These server-side changes and Trusted Publisher verification remain outstanding.
The new release validator refuses publication under the observed configuration.

## Administrative ruleset attestation

GitHub [omits `bypass_actors` from read-only ruleset responses](https://docs.github.com/en/rest/repos/rules#get-a-repository-ruleset).
An omitted or null field does not establish that bypasses are disabled. Release
accepts an explicit empty list for branch and tag-immutability rulesets; otherwise
hidden actors require signed administrative evidence. Nonempty bypasses on those
rulesets still reject publication, including when revealed by the audit.

The release `GITHUB_TOKEN` retains its read permissions. An administrator performs
the independent audit on a trusted workstation with Python and OpenSSL:

1. Generate an RSA signing key outside the checkout (for example,
   `openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out /secure/release-audit.pem`).
   Keep the private key under administrator control. Export its public key with
   `openssl pkey -in /secure/release-audit.pem -pubout -out .github/release-rulesets-public.pem`
   and add that public key through a reviewed PR to the protected release branch.
   The public key is pinned in the released source; a mutable variable cannot
   replace the trust anchor. No private key or admin token belongs in CI.
2. Configure a short-lived `RULESETS_ADMIN_TOKEN` for the audit process, scoped to
   this repository and able to expose ruleset bypasses. Run:

   ```bash
   python .github/scripts/audit_release_rulesets.py \
     --repository DolphinSoftEU/DolphinDesktop \
     --private-key /secure/release-audit.pem --output /secure/rulesets-attestation.json
   ```

3. Set repository variable `RELEASE_RULESETS_ATTESTATION` to the exact JSON envelope
   from that file. Only the signed public evidence enters Release. The helper
   refuses to sign if its own API response omits/nulls `bypass_actors`.
4. Run release preparation within the evidence lifetime (one hour by default;
   `--valid-hours` can extend it to at most 24 hours). Refresh the administrative
   audit after expiry or any ruleset change. The signature binds repository,
   expiry, visible policy fields and `updated_at` to the observed bypass lists.
   Release compares the live policy digest, including the update timestamp, so
   evidence for an older configuration cannot supply the missing bypass data.

Missing evidence/key, signature failure, wrong repository, expired evidence or a
configuration mismatch blocks preparation. The audit supplies visibility; it
does not waive source, review, checks, environment or Trusted Publisher gates.

## Release procedure

1. Update `pyproject.toml`, `src/dolphin_desktop/__init__.py`, and `CHANGELOG.md`
   consistently through a reviewed PR. Production accepts only canonical final
   `X.Y.Z`, without development, RC, post, local or epoch segments.
2. Merge the approved PR into the protected release branch. Wait for the **push**
   CI run for its exact merge SHA to complete successfully. A PR CI run, an older
   successful run, skipped jobs, or a run for another SHA cannot authorize release.
3. CI builds with locked tools only after all required test/security jobs pass,
   checks distribution metadata, installs the wheel in a fresh Windows venv,
   and tests import/CLI. It uploads `dolphin-desktop-dist-attempt-N` containing
   `dist/*.whl`, `dist/*.tar.gz`, and `provenance.json` with SHA, CI run/attempt
   identity and SHA256 hashes of both files.
4. For production, create and push the matching `vX.Y.Z` tag on that exact merge
   commit. For preview, dispatch Release from an allowed branch with `testpypi`.
   To retry production, dispatch from the existing matching tag with `pypi`.
   If CI is still running when the tag is pushed, release fails; retry after CI.
5. Release validates source review/protection and environments, then selects the
   latest successful push CI run for the exact SHA and approved branch. It checks
   every required job in the latest run attempt and selects only that attempt's
   artifact name. Older artifacts remain distinct; no overwrite is used. A partial
   rerun that does not rebuild the package for the new attempt requires rerunning
   the package job or the complete CI workflow. The immutable artifact ID and
   GitHub archive digest are verified before extracting the distributions.
   Manifest hashes and run/attempt identity must match. A missing or expired
   artifact is a hard failure; rerun CI instead of rebuilding in Release.
6. Review the prepared artifact and release evidence, then approve the target
   environment. This is the final human gate. The publishing job downloads by
   immutable artifact ID and verifies the anchored manifest hash and both file
   hashes immediately before the Trusted Publisher upload. Only publishing jobs
   receive OIDC permission; metadata is outside the package upload directory.
7. Check index smoke results, create a GitHub Release with changelog notes, and
   open the next Unreleased changelog section. For a broken release, yank it in
   the index and release a new version through the same process.

## Verification

Run `uv run --locked --group dev pytest tests/framework/test_ci_security.py
tests/framework/test_release_provenance.py -q` (one command). These tests cover
ref restrictions, real Git ancestry rejection, missing/failed/skipped/wrong-SHA
CI, fork/PR runs, insufficient review/protection, mutable tags, weak environments,
archive substitution/path traversal, and modified distributions or CI identity.

Before closing KAN-579, record the administrator setup and a hosted negative run
that stops before upload, followed by a successful protected-source preparation
run. Do not approve a package upload merely to test a rejection path. The local
suite cannot certify GitHub settings or PyPI/TestPyPI account configuration.
