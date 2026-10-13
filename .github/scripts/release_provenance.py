"""KAN-579: select only distributions produced by successful, protected-source CI."""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

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
    def __init__(self, repository: str, token: str, attested_rulesets: dict | None = None) -> None:
        self.base = f"https://api.github.com/repos/{repository}"
        self.token = token
        self.attested_rulesets = attested_rulesets

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


def ruleset_digest(ruleset: dict) -> str:
    # updated_at binds the signed bypass list to this live configuration,
    # including updates that affect only the otherwise hidden bypass list.
    fields = ("id", "target", "enforcement", "conditions", "rules", "updated_at")
    if any(field not in ruleset for field in fields):
        raise ValueError("Ruleset lacks the configuration required for administrative attestation")
    if not isinstance(ruleset["updated_at"], str) or not ruleset["updated_at"]:
        raise ValueError("Ruleset lacks a usable update timestamp for administrative attestation")
    projection = {field: ruleset[field] for field in fields}
    return hashlib.sha256(json.dumps(projection, sort_keys=True).encode()).hexdigest()


def load_ruleset_attestation(
    envelope: str, public_key: Path, repository: str, *, openssl: str = "openssl"
) -> dict:
    signed = json.loads(envelope)
    if not isinstance(signed, dict):
        raise ValueError("Administrative attestation envelope must be an object")
    payload = base64.b64decode(signed["payload"], validate=True)
    signature = base64.b64decode(signed["signature"], validate=True)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "payload.json").write_bytes(payload)
        (root / "signature").write_bytes(signature)
        subprocess.run(
            [
                openssl,
                "dgst",
                "-sha256",
                "-verify",
                str(public_key.resolve()),
                "-signature",
                str(root / "signature"),
                str(root / "payload.json"),
            ],
            check=True,
            capture_output=True,
        )
    evidence = json.loads(payload)
    if not isinstance(evidence, dict):
        raise ValueError("Administrative attestation payload must be an object")
    now = time.time()
    if (
        evidence.get("schema") != 1
        or evidence.get("repository") != repository
        or not isinstance(evidence.get("issued_at"), int)
        or not isinstance(evidence.get("expires_at"), int)
        or not evidence["issued_at"] <= now < evidence["expires_at"]
        or not 0 < evidence["expires_at"] - evidence["issued_at"] <= 86400
        or not isinstance(evidence.get("rulesets"), dict)
    ):
        raise ValueError(
            "Administrative ruleset attestation is invalid, expired or for another repo"
        )
    return evidence["rulesets"]


def ruleset_bypass_actors(api: GitHub, ruleset: dict) -> list:
    if "bypass_actors" in ruleset:
        actors = ruleset["bypass_actors"]
    else:
        attested = getattr(api, "attested_rulesets", None) or {}
        evidence = attested.get(str(ruleset.get("id")))
        if not isinstance(evidence, dict) or evidence.get("policy_sha256") != ruleset_digest(
            ruleset
        ):
            raise ValueError(
                "Ruleset bypass actors are hidden; matching signed admin audit required"
            )
        actors = evidence.get("bypass_actors")
    if not isinstance(actors, list) or any(not isinstance(actor, dict) for actor in actors):
        raise ValueError("Ruleset bypass actors must be an explicit list")
    return actors


def validate_branch(api: GitHub, branch: str) -> None:
    if api.get(f"/branches/{branch}").get("protected") is not True:
        raise ValueError(f"Unprotected release source: {branch}")
    rules = api.get(f"/rules/branches/{branch}")
    # Require rulesets rather than accepting a protection flag that might only
    # prevent deletion. Server-side rules must enforce review and required CI.
    trusted = []
    for rule in rules:
        ruleset = api.get(f"/rulesets/{rule['ruleset_id']}")
        if ruleset.get("enforcement") == "active" and ruleset_bypass_actors(api, ruleset) == []:
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
        if ruleset.get("enforcement") != "active":
            continue
        conditions = ruleset.get("conditions", {}).get("ref_name", {})
        included = any(
            pattern == "~ALL" or fnmatch.fnmatchcase(ref, pattern)
            for pattern in conditions.get("include", [])
        )
        excluded = any(fnmatch.fnmatchcase(ref, p) for p in conditions.get("exclude", []))
        if included and not excluded:
            actors = ruleset_bypass_actors(api, ruleset)
            types = {rule["type"] for rule in ruleset["rules"]}
            if not actors:
                rules.update(types)
            # Creation may have dedicated releasers; immutability may not.
            if (
                "creation" in types
                and actors
                and all(
                    actor.get("bypass_mode") == "always"
                    and (
                        actor.get("actor_type") in {"Team", "Integration"}
                        or (
                            actor.get("actor_type") == "RepositoryRole"
                            and actor.get("actor_id") == 5
                        )
                    )
                    for actor in actors
                )
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
    name = f"dolphin-desktop-dist-attempt-{run['run_attempt']}"
    artifacts = [a for a in artifacts if a["name"] == name]
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
        envelope = os.environ.get("RULESETS_ATTESTATION", "")
        attested = (
            load_ruleset_attestation(
                envelope, Path(".github/release-rulesets-public.pem"), repository
            )
            if envelope
            else None
        )
        api = GitHub(repository, os.environ["GH_TOKEN"], attested)
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
    except (ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as error:
        print(f"Release provenance rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
