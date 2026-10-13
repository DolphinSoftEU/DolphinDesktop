"""KAN-579: select only distributions produced by successful, protected-source CI."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import sys
import urllib.request

REQUIRED_JOBS = {
    "Unit / Python 3.11 / Windows",
    "Unit / Python 3.12 / Windows",
    "Unit / Python 3.13 / Windows",
    "Windows component tests",
    "Linux package install and import",
    "Build package",
    "Security gate (CVE / secrets / SAST) / ubuntu-latest",
    "Security gate (CVE / secrets / SAST) / windows-latest",
}


class GitHub:
    def __init__(self, repository: str, token: str) -> None:
        self.base = f"https://api.github.com/repos/{repository}"
        self.token = token

    def get(self, path: str):
        request = urllib.request.Request(
            self.base + path,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    def pages(self, path: str, key: str | None = None) -> list:
        items = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 101):
            data = self.get(f"{path}{separator}per_page=100&page={page}")
            batch = data[key] if key else data
            items.extend(batch)
            if len(batch) < 100:
                return items
        raise ValueError("GitHub pagination limit exceeded")


def validate_branch(api: GitHub, branch: str) -> None:
    if api.get(f"/branches/{branch}").get("protected") is not True:
        raise ValueError(f"Unprotected release source: {branch}")
    rules = api.get(f"/rules/branches/{branch}")
    # Require rulesets rather than accepting a protection flag that might only
    # prevent deletion. Server-side rules must enforce review and required CI.
    trusted = []
    for rule in rules:
        ruleset = api.get(f"/rulesets/{rule['ruleset_id']}")
        if ruleset.get("enforcement") == "active" and not ruleset.get("bypass_actors"):
            trusted.append(rule)
    if not any(
        rule["type"] == "pull_request"
        and rule.get("parameters", {}).get("required_approving_review_count", 0) >= 1
        and rule["parameters"].get("dismiss_stale_reviews_on_push") is True
        and rule["parameters"].get("require_last_push_approval") is True
        for rule in trusted
    ):
        raise ValueError(f"Release source {branch} lacks mandatory independent review")
    contexts = {
        check["context"]
        for rule in trusted
        if rule["type"] == "required_status_checks"
        and rule.get("parameters", {}).get("strict_required_status_checks_policy") is True
        for check in rule.get("parameters", {}).get("required_status_checks", [])
        if check.get("integration_id") == 15368  # GitHub Actions, not arbitrary statuses.
    }
    if not REQUIRED_JOBS <= contexts:
        raise ValueError(f"Release source {branch} lacks required GitHub Actions checks")


def validate_review(api: GitHub, repository: str, sha: str, branch: str) -> None:
    for pull in api.pages(f"/commits/{sha}/pulls"):
        if (
            not pull.get("merged_at")
            or pull.get("merge_commit_sha") != sha
            or pull.get("base", {}).get("ref") != branch
            or pull.get("base", {}).get("repo", {}).get("full_name") != repository
        ):
            continue
        latest = {}
        for review in api.pages(f"/pulls/{pull['number']}/reviews"):
            if review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
                latest[review["user"]["login"]] = review
        if not any(review["state"] == "CHANGES_REQUESTED" for review in latest.values()) and any(
            login != pull["user"]["login"]
            and review["state"] == "APPROVED"
            and review.get("commit_id") == pull["head"]["sha"]
            for login, review in latest.items()
        ):
            return
    raise ValueError("Release SHA must be the merge result of an independently approved PR")


def validate_tag(api: GitHub, ref: str) -> None:
    if not ref.startswith("refs/tags/"):
        return
    rules = set()
    creation_restricted = False
    for entry in api.pages("/rulesets?includes_parents=true"):
        if entry["target"] != "tag" or entry["enforcement"] != "active":
            continue
        ruleset = api.get(f"/rulesets/{entry['id']}")
        conditions = ruleset.get("conditions", {}).get("ref_name", {})
        included = any(
            pattern == "~ALL" or fnmatch.fnmatchcase(ref, pattern)
            for pattern in conditions.get("include", [])
        )
        excluded = any(fnmatch.fnmatchcase(ref, p) for p in conditions.get("exclude", []))
        if included and not excluded:
            actors = ruleset.get("bypass_actors", [])
            types = {rule["type"] for rule in ruleset["rules"]}
            if not actors:
                rules.update(types)
            # Creation may have dedicated releasers; immutability may not.
            if "creation" in types and all(
                actor.get("actor_type") in {"Team", "Integration"}
                or (actor.get("actor_type") == "RepositoryRole" and actor.get("actor_id") == 5)
                for actor in actors
            ):
                creation_restricted = True
    if not {"update", "deletion"} <= rules or not creation_restricted:
        raise ValueError(
            "Release tag requires restricted creation and immutable update/deletion rules"
        )


def validate_environment(api: GitHub, target: str) -> None:
    environment = api.get(f"/environments/{target}")
    if environment.get("can_admins_bypass") is not False:
        raise ValueError("Environment administrator bypass must be disabled")
    if not any(
        rule["type"] == "required_reviewers"
        and rule.get("prevent_self_review") is True
        and rule.get("reviewers")
        for rule in environment.get("protection_rules", [])
    ):
        raise ValueError("Environment must require independent approval")
    if environment.get("deployment_branch_policy") != {
        "protected_branches": False,
        "custom_branch_policies": True,
    }:
        raise ValueError("Environment must restrict selected branches and tags")
    policies = api.pages(f"/environments/{target}/deployment-branch-policies", "branch_policies")
    allowed = {("tag", "v*.*.*")}
    if target == "testpypi":
        allowed |= {("branch", "master"), ("branch", "testpypi-preview")}
    if {(p["type"], p["name"]) for p in policies} != allowed:
        raise ValueError("Unexpected environment deployment ref rules")


def select_artifact(api: GitHub, repository: str, sha: str, branch: str) -> dict:
    runs = api.pages(
        f"/actions/workflows/ci.yml/runs?head_sha={sha}&event=push&branch={branch}",
        "workflow_runs",
    )
    # Do not fall back to an old success when the latest run is failed/pending.
    runs = [
        run
        for run in runs
        if run.get("head_sha") == sha
        and run.get("head_branch") == branch
        and run.get("event") == "push"
        and run.get("repository", {}).get("full_name") == repository
        and run.get("head_repository", {}).get("full_name") == repository
        and run.get("path", "").split("@")[0] == ".github/workflows/ci.yml"
    ]
    if not runs:
        raise ValueError("No protected-branch push CI run for the exact release SHA")
    run = max(runs, key=lambda item: item["id"])
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ValueError("Latest CI run for the exact release SHA is not successful")
    jobs = api.pages(f"/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", "jobs")
    passed = {
        job["name"]
        for job in jobs
        if job.get("head_sha") == sha
        and job.get("status") == "completed"
        and job.get("conclusion") == "success"
    }
    if not REQUIRED_JOBS <= passed:
        raise ValueError("Required Windows CI/security jobs did not pass for this SHA")
    artifacts = api.pages(f"/actions/runs/{run['id']}/artifacts", "artifacts")
    artifacts = [a for a in artifacts if a["name"] == "dolphin-desktop-dist"]
    if len(artifacts) != 1:
        raise ValueError("Expected exactly one CI distribution artifact")
    artifact = artifacts[0]
    origin = artifact.get("workflow_run", {})
    if (
        artifact.get("expired") is not False
        or origin.get("id") != run["id"]
        or origin.get("head_sha") != sha
        or origin.get("head_branch") != branch
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", ""))
    ):
        raise ValueError("CI artifact lacks valid SHA/digest provenance or has expired")
    return {
        "ci_run_id": run["id"],
        "ci_run_attempt": run["run_attempt"],
        "artifact_id": artifact["id"],
        "artifact_digest": artifact["digest"],
    }


def main() -> int:
    try:
        repository = os.environ["GITHUB_REPOSITORY"]
        sha = os.environ["RELEASE_SHA"]
        ref = os.environ["RELEASE_REF"]
        target = os.environ["RELEASE_TARGET"]
        api = GitHub(repository, os.environ["GH_TOKEN"])
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid release SHA")
        checked_out = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        if checked_out != sha:
            raise ValueError("Checkout does not match release SHA")
        branch = "testpypi-preview" if ref == "refs/heads/testpypi-preview" else "master"
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, f"refs/remotes/origin/{branch}"],
            check=True,
        )
        validate_branch(api, branch)
        validate_review(api, repository, sha, branch)
        validate_tag(api, ref)
        validate_environment(api, target)
        result = select_artifact(api, repository, sha, branch)
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            for key, value in result.items():
                output.write(f"{key}={value}\n")
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(f"Release provenance rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
