# Releasing dolphin-desktop

This checklist takes a `master` branch commit to a PyPI release.

The normal publishing path is `.github/workflows/release.yml`: push a version
tag or dispatch the workflow with the desired target. CVE, secrets and SAST
checks on the exact release commit must pass on Windows and Linux before
building; only the publishing jobs receive an OIDC token. See the
[CI security and retest policy](SECURITY.md#github-actions-and-release-gates-kan-474).
The manual commands below are maintainer operations and do not enforce those
workflow gates.

Production PyPI publication requires a canonical final `X.Y.Z` version and
either `master` or the matching `vX.Y.Z` tag. The checked-out production commit
must be an ancestor of `origin/master`; an unmerged feature commit cannot be
published by attaching a release tag. Development, alpha, beta, RC, post and
local versions are rejected for PyPI. TestPyPI accepts `master`,
`testpypi-preview`, or a matching final/RC tag. An RC tag uses `vX.Y.Z-rc.N`
and must match the project's `X.Y.ZrcN` version.

Before using the workflow, an administrator must configure both environments
in [GitHub Settings → Environments](https://github.com/DolphinSoftEU/DolphinDesktop/settings/environments):

| Environment | Allowed branches | Allowed tags |
| --- | --- | --- |
| `pypi` | `master` | `v*.*.*` |
| `testpypi` | `master`, `testpypi-preview` | `v*.*.*` |

Select **Selected branches and tags** and create separate branch and tag rules;
remove any broader existing rules. The tag glob is supplemented by the stricter
version and source validation in the workflow. Enable **Prevent self-review**
with the existing required reviewer and disable **Allow administrators to bypass
configured protection rules** if approval must always be independent. The sole
current required reviewer is `DolphinSoftEU`: that account cannot approve a run
it initiates when self-review is prevented. Another collaborator must initiate
the run, or an administrator must appoint another trusted reviewer.

On 2026-10-11, applying these environment changes through the available GitHub
credentials failed with HTTP 403 (`Must have admin rights to Repository`). The
workflow validation is implemented locally, but the environment settings still
require an administrator. See [GitHub's environment configuration instructions](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

## 1 — Pre-flight

- [ ] All tests pass on the release commit (`pytest -q`).
- [ ] `CHANGELOG.md` has a section for the new version with dates and
      concrete "Added / Changed / Fixed" entries — no `TBD` bullets.
- [ ] Version is consistent everywhere:
      - `pyproject.toml` `[project] version`
      - `src/dolphin_desktop/__init__.py` `__version__`
      - `CHANGELOG.md` header
      - `docs/index.md` if it mentions a version explicitly

## 2 — Bump

```powershell
# From a clean checkout on master
$new = "0.2.0"

# 1. Update pyproject
(Get-Content pyproject.toml) -replace '^version = ".*"', "version = ""$new""" |
    Set-Content pyproject.toml

# 2. Update the module __version__
(Get-Content src/dolphin_desktop/__init__.py) -replace '^__version__ = ".*"', "__version__ = ""$new""" |
    Set-Content src/dolphin_desktop/__init__.py

# 3. Verify
Select-String -Path pyproject.toml -Pattern "^version"
Select-String -Path src/dolphin_desktop/__init__.py -Pattern "^__version__"

git diff --stat
```

## 3 — Build

```powershell
# Clean any previous builds
Remove-Item -Recurse -Force dist -ErrorAction SilentlyContinue

# Install the locked build tools and build without resolving a new backend
uv sync --locked --only-group build
uv run --no-sync python -m build --no-isolation

# Inspect the wheel contents
python -m zipfile -l dist\dolphin_desktop-$new-py3-none-any.whl

# Twine dry-run — validates the metadata
uv run --no-sync twine check dist\*
```

Both `twine check` lines should print `PASSED`.

## 4 — Tag and push

```powershell
git add pyproject.toml src/dolphin_desktop/__init__.py CHANGELOG.md
git commit -m "Release v$new"
git tag "v$new" -m "Release v$new"
git push origin master
git push origin "v$new"
```

## 5 — Upload

Test upload to TestPyPI first:

```powershell
python -m twine upload --repository testpypi dist\*
```

Verify by installing the test wheel in a fresh venv:

```powershell
python -m venv .test-install
.\.test-install\Scripts\Activate.ps1
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ "dolphin-desktop==$new"
python -c "import dolphin_desktop; print(dolphin_desktop.__version__)"
deactivate
```

If that succeeds, upload to real PyPI:

```powershell
python -m twine upload dist\*
```

## 6 — Post-release

- [ ] Create a GitHub Release from the tag; paste the `CHANGELOG.md`
      section for this version as the release notes.
- [ ] Open a new "Unreleased" section at the top of `CHANGELOG.md`.
- [ ] If the docs site is versioned (mike), publish the new version
      with `mike deploy $new latest --push`.

## Credentials

Twine reads `%USERPROFILE%\.pypirc`:

```ini
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = pypi-XXXX...

[testpypi]
repository = https://test.pypi.org/legacy/
username = __token__
password = pypi-XXXX...
```

Generate PyPI tokens at:

* PyPI: <https://pypi.org/manage/account/token/>
* TestPyPI: <https://test.pypi.org/manage/account/token/>

Scope tokens to the `dolphin-desktop` project only.

## Rolling back

If a broken wheel makes it to PyPI:

```powershell
# You cannot re-upload the same version. Yank instead:
# https://pypi.org/manage/project/dolphin-desktop/releases/
# → click the version → "Yank release"

# Then bump the patch and re-release with the fix.
```

Yanking hides the version from `pip install dolphin-desktop`
(non-pinned installs skip it) but pinned installs (`==0.2.0`) still
work — so existing consumers do not break.
