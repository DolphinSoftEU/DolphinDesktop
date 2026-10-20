"""KAN-579: reject untested sources, weak protection and substituted artifacts."""

from __future__ import annotations

import hashlib
import io
import json
import runpy
import sys
import zipfile
from copy import deepcopy
from pathlib import Path
from urllib.request import Request

import pytest

pytestmark = pytest.mark.unit
SCRIPTS = Path(__file__).resolve().parents[2] / ".github/scripts"
provenance = runpy.run_path(str(SCRIPTS / "release_provenance.py"))
manifest = runpy.run_path(str(SCRIPTS / "distribution_manifest.py"))
download = runpy.run_path(str(SCRIPTS / "download_ci_artifact.py"))
SHA = "a" * 40
REPO = "DolphinSoftEU/DolphinDesktop"
JOBS = provenance["REQUIRED_JOBS"]

sys.path.insert(0, str(SCRIPTS))
policy = runpy.run_path(str(SCRIPTS / "release_policy.py"))


class API:
    def __init__(self):
        self.run = {
            "id": 42,
            "run_attempt": 2,
            "head_sha": SHA,
            "head_branch": "master",
            "event": "push",
            "path": ".github/workflows/ci.yml",
            "repository": {"full_name": REPO},
            "head_repository": {"full_name": REPO},
            "status": "completed",
            "conclusion": "success",
        }
        self.jobs = [
            {"name": name, "head_sha": SHA, "status": "completed", "conclusion": "success"}
            for name in JOBS
        ]
        self.artifact = {
            "id": 7,
            "name": "dolphin-desktop-dist-attempt-2",
            "expired": False,
            "digest": "sha256:" + "b" * 64,
            "workflow_run": {"id": 42, "head_sha": SHA, "head_branch": "master"},
        }
        self.rules = [
            {
                "type": "pull_request",
                "ruleset_id": 1,
                "parameters": {
                    "required_approving_review_count": 1,
                    "dismiss_stale_reviews_on_push": True,
                    "require_last_push_approval": True,
                },
            },
            {
                "type": "required_status_checks",
                "ruleset_id": 1,
                "parameters": {
                    "strict_required_status_checks_policy": True,
                    "required_status_checks": [
                        {"context": n, "integration_id": 15368} for n in JOBS
                    ],
                },
            },
        ]
        self.ruleset = {
            "id": 1,
            "target": "branch",
            "enforcement": "active",
            "bypass_actors": [],
            "conditions": {"ref_name": {"include": ["refs/heads/master"], "exclude": []}},
            "rules": self.rules,
            "updated_at": "2026-10-13T12:00:00Z",
        }
        self.environment = {
            "can_admins_bypass": False,
            "protection_rules": [
                {
                    "type": "required_reviewers",
                    "prevent_self_review": True,
                    "reviewers": [{"type": "User"}],
                }
            ],
            "deployment_branch_policy": {
                "protected_branches": False,
                "custom_branch_policies": True,
            },
        }
        self.policies = [{"type": "tag", "name": "v*.*.*"}]
        self.tag = {
            "id": 2,
            "target": "tag",
            "updated_at": "2026-10-13T12:00:00Z",
            "enforcement": "active",
            "bypass_actors": [],
            "conditions": {"ref_name": {"include": ["refs/tags/v*.*.*"], "exclude": []}},
            "rules": [{"type": "update"}, {"type": "deletion"}],
        }
        self.creation = deepcopy(self.tag)
        self.creation["id"] = 3
        self.creation["rules"] = [{"type": "creation"}]
        self.creation["bypass_actors"] = [
            {"actor_type": "Team", "actor_id": 7, "bypass_mode": "always"},
        ]
        self.protected = True

    def get(self, path):
        if path == "/branches/master":
            return {"protected": self.protected}
        if path == "/rules/branches/master":
            return self.rules
        if path == "/rulesets/1":
            return self.ruleset
        if path == "/rulesets/2":
            return self.tag
        if path == "/rulesets/3":
            return self.creation
        if path == "/environments/pypi":
            return self.environment
        raise AssertionError(path)

    def pages(self, path, key=None):
        if path.startswith("/actions/workflows/ci.yml/runs?"):
            assert f"head_sha={SHA}&event=push&branch=master" in path
            return [self.run]
        if path == "/actions/runs/42/attempts/2/jobs":
            return self.jobs
        if path == "/actions/runs/42/artifacts":
            return [self.artifact]
        if path == "/environments/pypi/deployment-branch-policies":
            return self.policies
        if path == "/rulesets?includes_parents=true":
            return [{"id": n, "target": "tag", "enforcement": "active"} for n in (2, 3)]
        raise AssertionError(path)


def test_verified_ci_artifact_is_selected():
    api = API()
    assert policy["validate_branch"](api, "master") == "verified"
    assert policy["validate_tag"](api, "refs/tags/v0.2.0") == "verified"
    policy["validate_environment"](api, "pypi")
    assert provenance["select_artifact"](api, REPO, SHA, "master") == {
        "ci_run_id": 42,
        "ci_run_attempt": 2,
        "artifact_id": 7,
        "artifact_digest": api.artifact["digest"],
    }


@pytest.mark.parametrize(
    "change",
    [
        "sha",
        "fork",
        "pr",
        "branch",
        "failed",
        "pending",
        "workflow",
        "job-sha",
        "skipped-security",
        "missing-windows",
        "expired",
        "digest",
        "artifact-sha",
        "artifact-run",
        "artifact-branch",
    ],
)
def test_unverified_ci_or_artifact_cannot_be_selected(change):
    api = API()
    if change == "sha":
        api.run["head_sha"] = "c" * 40
    elif change == "fork":
        api.run["head_repository"]["full_name"] = "attacker/fork"
    elif change == "pr":
        api.run["event"] = "pull_request"
    elif change == "branch":
        api.run["head_branch"] = "feature"
    elif change == "failed":
        api.run["conclusion"] = "failure"
    elif change == "pending":
        api.run["status"] = "in_progress"
    elif change == "workflow":
        api.run["path"] = ".github/workflows/other.yml"
    elif change == "job-sha":
        api.jobs[0]["head_sha"] = "c" * 40
    elif change == "skipped-security":
        next(j for j in api.jobs if j["name"].startswith("Security"))["conclusion"] = "skipped"
    elif change == "missing-windows":
        api.jobs = [j for j in api.jobs if j["name"] != "Windows component tests"]
    elif change == "expired":
        api.artifact["expired"] = True
    elif change == "digest":
        api.artifact.pop("digest")
    elif change == "artifact-sha":
        api.artifact["workflow_run"]["head_sha"] = "c" * 40
    elif change == "artifact-run":
        api.artifact["workflow_run"]["id"] = 41
    elif change == "artifact-branch":
        api.artifact["workflow_run"]["head_branch"] = "feature"
    with pytest.raises(ValueError):
        provenance["select_artifact"](api, REPO, SHA, "master")


@pytest.mark.parametrize("system", ["ubuntu-latest", "macos-latest", "windows-latest"])
@pytest.mark.parametrize("python", ["3.11", "3.12", "3.13"])
@pytest.mark.parametrize("outcome", ["missing", "skipped", "failure"])
def test_every_native_wheel_check_must_pass_before_artifact_selection(system, python, outcome):
    api = API()
    name = f"Wheel install / {system} / Python {python}"
    if outcome == "missing":
        api.jobs = [job for job in api.jobs if job["name"] != name]
    else:
        next(job for job in api.jobs if job["name"] == name)["conclusion"] = outcome
    with pytest.raises(ValueError, match="Required CI/security/platform jobs"):
        provenance["select_artifact"](api, REPO, SHA, "master")


def test_latest_failed_run_cannot_fall_back_to_old_success():
    api = API()
    older = deepcopy(api.run)
    older["id"] = 41
    api.run["conclusion"] = "failure"
    original = api.pages
    api.pages = lambda path, key=None: (
        [older, api.run] if path.startswith("/actions/workflows") else original(path, key)
    )
    with pytest.raises(ValueError, match="Latest CI"):
        provenance["select_artifact"](api, REPO, SHA, "master")


@pytest.mark.parametrize(
    "change",
    [
        "unprotected",
        "no-review",
        "stale",
        "self-review",
        "no-checks",
        "spoofed-checks",
        "bypass",
        "disabled",
    ],
)
def test_weak_branch_protection_is_rejected(change):
    api = API()
    if change == "unprotected":
        api.protected = False
    elif change == "no-review":
        api.rules[0]["parameters"]["required_approving_review_count"] = 0
    elif change == "stale":
        api.rules[0]["parameters"]["dismiss_stale_reviews_on_push"] = False
    elif change == "self-review":
        api.rules[0]["parameters"]["require_last_push_approval"] = False
    elif change == "no-checks":
        api.rules.pop()
    elif change == "spoofed-checks":
        api.rules[1]["parameters"]["required_status_checks"][0]["integration_id"] = None
    elif change == "bypass":
        api.ruleset["bypass_actors"] = [{"actor_id": 5}]
    elif change == "disabled":
        api.ruleset["enforcement"] = "evaluate"
    with pytest.raises(ValueError):
        policy["validate_branch"](api, "master")


@pytest.mark.parametrize(
    "change",
    ["admin", "self-review", "reviewers", "unrestricted", "broad-tags", "production-branch"],
)
def test_weak_environment_is_rejected(change):
    api = API()
    if change == "admin":
        api.environment["can_admins_bypass"] = True
    elif change == "self-review":
        api.environment["protection_rules"][0]["prevent_self_review"] = False
    elif change == "reviewers":
        api.environment["protection_rules"] = []
    elif change == "unrestricted":
        api.environment["deployment_branch_policy"] = None
    elif change == "broad-tags":
        api.policies[0]["name"] = "*"
    elif change == "production-branch":
        api.policies.append({"type": "branch", "name": "master"})
    with pytest.raises(ValueError):
        policy["validate_environment"](api, "pypi")


@pytest.mark.parametrize(
    "change", ["no-update", "no-deletion", "exclude", "wrong-ref", "bypass", "no-creation"]
)
def test_mutable_or_unprotected_tags_are_rejected(change):
    api = API()
    if change == "no-update":
        api.tag["rules"].pop(0)
    elif change == "no-deletion":
        api.tag["rules"].pop(1)
    elif change == "no-creation":
        api.creation["rules"] = []
    elif change == "exclude":
        api.tag["conditions"]["ref_name"]["exclude"] = ["refs/tags/v0.2.0"]
    elif change == "wrong-ref":
        api.tag["conditions"]["ref_name"]["include"] = ["refs/tags/preview*"]
    elif change == "bypass":
        api.tag["bypass_actors"] = [{"actor_id": 5}]
    with pytest.raises(ValueError):
        policy["validate_tag"](api, "refs/tags/v0.2.0")


@pytest.mark.parametrize("change", ["none", "wheel", "sdist", "extra", "sha", "run", "attempt"])
def test_distribution_manifest_binds_every_file_and_ci_identity(tmp_path, change):
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "fixture-0.2.0-py3-none-any.whl"
    wheel.write_bytes(b"verified wheel")
    sdist = dist / "fixture-0.2.0.tar.gz"
    sdist.write_bytes(b"verified sdist")
    data = manifest["manifest"](dist, SHA, "42", "2")
    if change == "wheel":
        wheel.write_bytes(b"substituted wheel")
    elif change == "sdist":
        sdist.write_bytes(b"substituted sdist")
    elif change == "extra":
        (dist / "extra.whl").write_bytes(b"unexpected")
    elif change == "sha":
        data["sha"] = "c" * 40
    elif change == "run":
        data["ci_run_id"] = "41"
    elif change == "attempt":
        data["ci_run_attempt"] = "1"
    metadata = tmp_path / "provenance.json"
    metadata.write_text(json.dumps(data), encoding="utf-8")
    if change == "none":
        manifest["verify"](dist, metadata, SHA, "42", "2")
    else:
        with pytest.raises(ValueError):
            manifest["verify"](dist, metadata, SHA, "42", "2")


@pytest.mark.parametrize("change", ["none", "digest", "traversal", "duplicate"])
def test_archive_digest_and_paths_are_enforced(tmp_path, change):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("provenance.json", "{}")
        archive.writestr("dist/fixture.whl", b"wheel")
        archive.writestr("../outside" if change == "traversal" else "dist/fixture.tar.gz", b"sdist")
        if change == "duplicate":
            archive.writestr("dist/extra.whl", b"extra")
    data = buffer.getvalue()
    digest = "sha256:" + hashlib.sha256(data).hexdigest()
    if change == "digest":
        digest = "sha256:" + "0" * 64
    if change == "none":
        download["unpack"](data, digest, tmp_path / "artifact")
        assert (tmp_path / "artifact/dist/fixture.whl").read_bytes() == b"wheel"
    else:
        with pytest.raises(ValueError):
            download["unpack"](data, digest, tmp_path / "artifact")
        assert not (tmp_path / "artifact").exists()


def test_archive_redirect_does_not_forward_github_token():
    request = Request("https://api.github.com/artifact", headers={"Authorization": "Bearer secret"})
    redirected = download["NoAuthRedirect"]().redirect_request(
        request, None, 302, "Found", {}, "https://storage.example/archive"
    )
    assert redirected is not None
    assert not redirected.has_header("Authorization")


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "direct-push",
        "wrong-merge",
        "wrong-branch",
        "self-review",
        "stale",
        "dismissed",
        "changes-requested",
        "unmerged",
        "wrong-repo",
    ],
)
def test_exact_merge_result_requires_current_independent_review(change):
    pull = {
        "number": 12,
        "merged_at": "2026-10-13T12:00:00Z",
        "merge_commit_sha": SHA,
        "base": {"ref": "master", "repo": {"full_name": REPO}},
        "head": {"sha": "d" * 40},
        "user": {"login": "author"},
    }
    reviews = [{"id": 1, "user": {"login": "reviewer"}, "state": "APPROVED", "commit_id": "d" * 40}]
    if change == "wrong-merge":
        pull["merge_commit_sha"] = "e" * 40
    elif change == "wrong-branch":
        pull["base"]["ref"] = "feature"
    elif change == "self-review":
        reviews[0]["user"]["login"] = "author"
    elif change == "stale":
        reviews[0]["commit_id"] = "e" * 40
    elif change in {"dismissed", "changes-requested"}:
        later = deepcopy(reviews[0])
        later["id"] = 2
        later["state"] = "DISMISSED" if change == "dismissed" else "CHANGES_REQUESTED"
        reviews.append(later)
    elif change == "unmerged":
        pull["merged_at"] = None
    elif change == "wrong-repo":
        pull["base"]["repo"]["full_name"] = "attacker/fork"
    api = API()
    api.pages = lambda path, key=None: (
        ([] if change == "direct-push" else [pull]) if path == f"/commits/{SHA}/pulls" else reviews
    )
    if change == "none":
        policy["validate_review"](api, REPO, SHA, "master")
    else:
        with pytest.raises(ValueError, match="independently approved PR"):
            policy["validate_review"](api, REPO, SHA, "master")


def test_ci_artifact_is_built_only_after_required_checks_and_smoke_test():
    import yaml

    root = SCRIPTS.parents[1]
    workflow = yaml.load(
        (root / ".github/workflows/ci.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )
    package = workflow["jobs"]["package"]
    assert set(package["needs"]) == {
        "unit",
        "component-windows",
        "package-linux",
        "security",
        "minimum-dependencies",
    }
    assert "if" not in package and "continue-on-error" not in package
    names = [step.get("name") for step in package["steps"]]
    assert (
        names.index("Build wheel and sdist")
        < names.index("Check distribution metadata")
        < names.index("Verify built wheel on Windows")
        < names.index("Record distribution hashes and CI identity")
        < names.index("Upload package artifacts")
    )
    for step in package["steps"]:
        assert "if" not in step and "continue-on-error" not in step
    upload = package["steps"][-1]
    assert upload["with"]["name"] == "dolphin-desktop-dist-attempt-${{ github.run_attempt }}"
    assert "overwrite" not in upload["with"]
    release = yaml.load(
        (root / ".github/workflows/release.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )
    prepared = release["jobs"]["build"]["steps"][-1]
    assert prepared["with"]["name"] == "dolphin-desktop-dist-attempt-${{ github.run_attempt }}"
    assert "overwrite" not in prepared["with"]


@pytest.mark.parametrize("substituted", [False, True])
def test_publish_cannot_replace_both_manifest_and_wheel(tmp_path, monkeypatch, substituted):
    dist = tmp_path / "release-artifact/dist"
    dist.mkdir(parents=True)
    wheel = dist / "fixture.whl"
    wheel.write_bytes(b"verified wheel")
    (dist / "fixture.tar.gz").write_bytes(b"verified sdist")
    metadata = dist.parent / "provenance.json"
    metadata.write_text(json.dumps(manifest["manifest"](dist, SHA, "42", "2")), encoding="utf-8")
    approved_hash = hashlib.sha256(metadata.read_bytes()).hexdigest()
    if substituted:
        wheel.write_bytes(b"substituted wheel")
        metadata.write_text(
            json.dumps(manifest["manifest"](dist, SHA, "42", "2")), encoding="utf-8"
        )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RELEASE_SHA", SHA)
    monkeypatch.setenv("CI_RUN_ID", "42")
    monkeypatch.setenv("CI_RUN_ATTEMPT", "2")
    monkeypatch.setenv("MANIFEST_SHA256", approved_hash)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setattr(manifest["sys"], "argv", ["distribution_manifest.py", "verify"])
    assert manifest["main"]() == (1 if substituted else 0)
    assert (tmp_path / "output").exists() is not substituted


@pytest.mark.parametrize("latest_exists", [True, False])
def test_ci_rerun_selects_only_the_verified_attempt_artifact(latest_exists):
    api = API()
    old = deepcopy(api.artifact)
    old["id"] = 6
    old["name"] = "dolphin-desktop-dist-attempt-1"
    original = api.pages
    api.pages = lambda path, key=None: (
        ([old, api.artifact] if latest_exists else [old])
        if path == "/actions/runs/42/artifacts"
        else original(path, key)
    )
    if latest_exists:
        assert provenance["select_artifact"](api, REPO, SHA, "master")["artifact_id"] == 7
    else:
        with pytest.raises(ValueError, match="exactly one CI distribution artifact"):
            provenance["select_artifact"](api, REPO, SHA, "master")


@pytest.mark.parametrize("area", ["branch", "tag", "creation"])
@pytest.mark.parametrize("visibility", ["missing", "null"])
def test_read_only_rulesets_do_not_require_manual_evidence(area, visibility):
    api = API()
    ruleset = {"branch": api.ruleset, "tag": api.tag, "creation": api.creation}[area]
    if visibility == "missing":
        ruleset.pop("bypass_actors")
    else:
        ruleset["bypass_actors"] = None
    assert policy["validate_branch"](api, "master") == (
        "unknown" if area == "branch" else "verified"
    )
    assert policy["validate_tag"](api, "refs/tags/v0.2.0") == (
        "unknown" if area != "branch" else "verified"
    )


@pytest.mark.parametrize(
    "actors,expected",
    [
        (None, "unknown"),
        ([], "invalid"),
        ([{"actor_type": "Team", "actor_id": 7, "bypass_mode": "always"}], "verified"),
        ([{"actor_type": "RepositoryRole", "actor_id": 4, "bypass_mode": "always"}], "invalid"),
        ([{"actor_type": "Team", "actor_id": 7, "bypass_mode": "pull_request"}], "invalid"),
        ([{"actor_type": "Team", "bypass_mode": "always"}], "invalid"),
    ],
)
def test_tag_creation_authorization_has_three_distinct_states(actors, expected):
    api = API()
    api.creation["bypass_actors"] = actors
    assert policy["bypass_status"](api.creation, creation=True) == expected
    if expected == "invalid":
        with pytest.raises(ValueError, match="dedicated authorized releasers"):
            policy["validate_tag"](api, "refs/tags/v0.2.0")
    else:
        assert policy["validate_tag"](api, "refs/tags/v0.2.0") == expected


@pytest.mark.parametrize("outcome", ["verified", "unknown", "invalid"])
def test_preflight_reports_status_in_logs_outputs_and_summary(
    tmp_path, monkeypatch, capsys, outcome
):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    preflight = runpy.run_path(str(SCRIPTS / "release_preflight.py"))
    main = preflight["main"]
    status = preflight["PolicyStatus"](outcome)
    output, summary = tmp_path / "outputs", tmp_path / "summary"
    for key, value in {
        "GITHUB_REPOSITORY": REPO,
        "GH_TOKEN": "fake-token",
        "RELEASE_SHA": SHA,
        "RELEASE_REF": "refs/tags/v0.2.0",
        "RELEASE_TARGET": "pypi",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
    }.items():
        monkeypatch.setenv(key, value)

    def audit(*args):
        if outcome == "invalid":
            raise ValueError("Invalid creation policy")
        return status

    monkeypatch.setitem(main.__globals__, "audit", audit)
    assert main() == (1 if outcome == "invalid" else 0)
    logs = capsys.readouterr()
    assert f"policy status: {outcome}" in logs.out
    assert output.read_text(encoding="utf-8") == f"policy_status={outcome}\n"
    assert f"policy status: {outcome}" in summary.read_text(encoding="utf-8")
    if outcome == "unknown":
        assert "authorization is NOT verified" in logs.out
        assert "preflight passed" not in logs.out
    assert "fake-token" not in logs.out + logs.err


def test_preview_allows_author_merge_without_second_account():
    api = API()
    api.rules[0]["parameters"] = {"required_approving_review_count": 0}
    original = api.get
    api.get = lambda path: (
        {"protected": True}
        if path == "/branches/testpypi-preview"
        else api.rules
        if path == "/rules/branches/testpypi-preview"
        else original(path)
    )
    policy["validate_branch"](api, "testpypi-preview")
    with pytest.raises(ValueError, match="independent review"):
        policy["validate_branch"](api, "master")


@pytest.mark.parametrize("change", ["none", "reviewers", "tag", "feature", "admin"])
def test_preview_environment_needs_branch_restrictions_without_approval(change):
    api = API()
    api.environment["protection_rules"] = []
    api.policies = [
        {"type": "branch", "name": "master"},
        {"type": "branch", "name": "testpypi-preview"},
    ]
    if change == "reviewers":
        api.environment["protection_rules"] = [{"type": "required_reviewers"}]
    elif change == "tag":
        api.policies.append({"type": "tag", "name": "v*.*.*"})
    elif change == "feature":
        api.policies.append({"type": "branch", "name": "*"})
    elif change == "admin":
        api.environment["can_admins_bypass"] = True
    original_get, original_pages = api.get, api.pages
    api.get = lambda path: original_get(path.replace("testpypi", "pypi"))
    api.pages = lambda path, key=None: original_pages(path.replace("testpypi", "pypi"), key)
    if change == "none":
        policy["validate_environment"](api, "testpypi")
    else:
        with pytest.raises(ValueError):
            policy["validate_environment"](api, "testpypi")


@pytest.mark.parametrize("key", ["id", "run_attempt"])
@pytest.mark.parametrize("value", [None, 0, -1, "2", True])
def test_missing_or_invalid_ci_identity_is_rejected(key, value):
    api = API()
    api.run[key] = value
    with pytest.raises(ValueError):
        provenance["select_artifact"](api, REPO, SHA, "master")


def test_preflight_has_no_publish_permissions_or_environment():
    import yaml

    root = SCRIPTS.parents[1]
    workflow = yaml.load(
        (root / ".github/workflows/release-preflight.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["release-preflight"]
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert "environment" not in job
    assert set(job["permissions"].values()) == {"read"}
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    for script in (
        "validate_release",
        "release_preflight",
        "release_provenance",
        "download_ci_artifact",
        "distribution_manifest",
        "smoke_wheel",
    ):
        assert f"{script}.py" in commands
    assert not any("pypi-publish" in step.get("uses", "") for step in job["steps"])
    release = (root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert "RULESETS_ATTESTATION" not in release
    assert not (root / ".github/scripts/audit_release_rulesets.py").exists()
    assert not (root / ".github/release-rulesets-public.pem").exists()


@pytest.mark.parametrize("change", ["none", "checkout", "ref", "ci", "sha"])
def test_provenance_cli_emits_outputs_only_for_verified_source(tmp_path, monkeypatch, change):
    api = API()
    output = tmp_path / "outputs"
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for key, value in {
        "GITHUB_REPOSITORY": REPO,
        "GH_TOKEN": "not-a-real-token",
        "RELEASE_SHA": SHA,
        "RELEASE_REF": "refs/heads/master",
        "RELEASE_TARGET": "testpypi",
        "EVENT_NAME": "workflow_dispatch",
        "GITHUB_OUTPUT": str(output),
    }.items():
        monkeypatch.setenv(key, value)
    main = provenance["main"]
    monkeypatch.setitem(main.__globals__, "GitHub", lambda *args: api)
    monkeypatch.setattr(
        provenance["subprocess"],
        "check_output",
        lambda *args, **kwargs: "c" * 40 if change == "checkout" else SHA,
    )
    monkeypatch.setattr(provenance["subprocess"], "run", lambda *args, **kwargs: None)
    if change == "ref":
        monkeypatch.setenv("RELEASE_REF", "refs/heads/feature")
    elif change == "ci":
        api.run["conclusion"] = "failure"
    elif change == "sha":
        monkeypatch.setenv("RELEASE_SHA", "invalid")
    assert main() == (0 if change == "none" else 1)
    if change == "none":
        assert output.read_text(encoding="utf-8") == (
            "ci_run_id=42\nci_run_attempt=2\nartifact_id=7\n"
            f"artifact_digest={api.artifact['digest']}\n"
        )
    else:
        assert not output.exists()
