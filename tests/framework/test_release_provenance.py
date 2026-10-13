"""KAN-579: reject untested sources, weak protection and substituted artifacts."""

from __future__ import annotations

import hashlib
import io
import json
import runpy
import zipfile
from copy import deepcopy
from pathlib import Path
from urllib.request import Request

import pytest

pytestmark = pytest.mark.unit
SCRIPTS = Path(__file__).resolve().parents[2] / ".github/scripts"
policy = runpy.run_path(str(SCRIPTS / "release_provenance.py"))
manifest = runpy.run_path(str(SCRIPTS / "distribution_manifest.py"))
download = runpy.run_path(str(SCRIPTS / "download_ci_artifact.py"))
SHA = "a" * 40
REPO = "DolphinSoftEU/DolphinDesktop"
JOBS = policy["REQUIRED_JOBS"]


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
            "name": "dolphin-desktop-dist",
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
        self.ruleset = {"enforcement": "active", "bypass_actors": []}
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
            "enforcement": "active",
            "bypass_actors": [],
            "conditions": {"ref_name": {"include": ["refs/tags/v*.*.*"], "exclude": []}},
            "rules": [{"type": "update"}, {"type": "deletion"}, {"type": "creation"}],
        }
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
            return [{"id": 2, "target": "tag", "enforcement": "active"}]
        raise AssertionError(path)


def test_verified_ci_artifact_is_selected():
    api = API()
    policy["validate_branch"](api, "master")
    policy["validate_tag"](api, "refs/tags/v0.2.0")
    policy["validate_environment"](api, "pypi")
    assert policy["select_artifact"](api, REPO, SHA, "master") == {
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
        policy["select_artifact"](api, REPO, SHA, "master")


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
        policy["select_artifact"](api, REPO, SHA, "master")


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
        api.tag["rules"].pop(2)
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
    assert set(package["needs"]) == {"unit", "component-windows", "package-linux", "security"}
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
