"""Read-only preflight of native GitHub policies; GitHub enforces the policies.

Read-only tokens may omit bypass actors. Absence is not evidence of no bypass:
we inspect visible actors when available and leave enforcement to GitHub.
No signed evidence, elevated token or private key is needed in Release.
"""

from __future__ import annotations

import fnmatch
from enum import StrEnum

from release_provenance import REQUIRED_JOBS


class PolicyStatus(StrEnum):
    VERIFIED = "verified"
    UNKNOWN = "unknown"
    INVALID = "invalid"


def visible_bypasses(ruleset: dict) -> list | None:
    actors = ruleset.get("bypass_actors")
    if actors is not None and (
        not isinstance(actors, list) or any(not isinstance(actor, dict) for actor in actors)
    ):
        raise ValueError("Malformed ruleset bypass actors")
    return actors


def bypass_status(ruleset: dict, *, creation: bool = False) -> PolicyStatus:
    actors = visible_bypasses(ruleset)
    if actors is None:
        return PolicyStatus.UNKNOWN
    if not creation:
        return PolicyStatus.INVALID if actors else PolicyStatus.VERIFIED
    if actors and all(
        actor.get("bypass_mode") == "always"
        and type(actor.get("actor_id")) is int
        and actor["actor_id"] > 0
        and (
            actor.get("actor_type") in {"Team", "Integration"}
            or (actor.get("actor_type") == "RepositoryRole" and actor["actor_id"] == 5)
        )
        for actor in actors
    ):
        return PolicyStatus.VERIFIED
    return PolicyStatus.INVALID


def validate_branch(api, branch: str) -> PolicyStatus:
    if branch not in {"master", "testpypi-preview"}:
        raise ValueError("Unexpected release source branch")
    if api.get(f"/branches/{branch}").get("protected") is not True:
        raise ValueError(f"Unprotected release source: {branch}")
    rules = api.get(f"/rules/branches/{branch}")
    # Require rulesets rather than accepting a protection flag that might only
    # prevent deletion. Server-side rules must enforce review and required CI.
    trusted = []
    rulesets = {}
    status = PolicyStatus.VERIFIED
    for rule in rules:
        identity = rule["ruleset_id"]
        if identity not in rulesets:
            rulesets[identity] = api.get(f"/rulesets/{identity}")
        ruleset = rulesets[identity]
        if ruleset.get("enforcement") == "active":
            bypass = bypass_status(ruleset)
            if bypass == PolicyStatus.INVALID:
                raise ValueError(f"Release source {branch} permits ruleset bypass")
            if bypass == PolicyStatus.UNKNOWN:
                status = PolicyStatus.UNKNOWN
            trusted.append(rule)
    if not any(
        rule["type"] == "pull_request"
        and (
            (
                branch == "testpypi-preview"
                and rule.get("parameters", {}).get("required_approving_review_count") == 0
                and not rule["parameters"].get("require_last_push_approval")
                and not rule["parameters"].get("require_code_owner_review")
            )
            or (
                branch == "master"
                and rule.get("parameters", {}).get("required_approving_review_count", 0) >= 1
                and rule["parameters"].get("dismiss_stale_reviews_on_push") is True
                and rule["parameters"].get("require_last_push_approval") is True
            )
        )
        for rule in trusted
    ):
        raise ValueError(f"Release source {branch} has an unexpected PR/independent review policy")
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
    return status


def validate_review(api, repository: str, sha: str, branch: str) -> None:
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


def validate_tag(api, ref: str) -> PolicyStatus:
    if not ref.startswith("refs/tags/"):
        return PolicyStatus.VERIFIED
    rules = set()
    creation_restricted = False
    status = PolicyStatus.VERIFIED
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
            types = {rule["type"] for rule in ruleset["rules"]}
            if types & {"update", "deletion"}:
                bypass = bypass_status(ruleset)
                if bypass == PolicyStatus.INVALID:
                    raise ValueError("Release tag immutability permits ruleset bypass")
                if bypass == PolicyStatus.UNKNOWN:
                    status = PolicyStatus.UNKNOWN
                rules.update(types & {"update", "deletion"})
            if "creation" in types:
                bypass = bypass_status(ruleset, creation=True)
                if bypass == PolicyStatus.INVALID:
                    raise ValueError("Release tag creation lacks dedicated authorized releasers")
                if bypass == PolicyStatus.UNKNOWN:
                    status = PolicyStatus.UNKNOWN
                # Rule presence is known; hidden releaser authorization stays unknown.
                creation_restricted = True
    if not {"update", "deletion"} <= rules or not creation_restricted:
        raise ValueError(
            "Release tag requires restricted creation and immutable update/deletion rules"
        )
    return status


def validate_environment(api, target: str) -> None:
    if target not in {"testpypi", "pypi"}:
        raise ValueError("Unknown release environment")
    environment = api.get(f"/environments/{target}")
    if environment.get("can_admins_bypass") is not False:
        raise ValueError("Environment administrator bypass must be disabled")
    if target == "testpypi" and any(
        rule["type"] == "required_reviewers" for rule in environment.get("protection_rules", [])
    ):
        raise ValueError("Preview environment must not require manual approval")
    if target == "pypi" and not any(
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
        allowed = {("branch", "master"), ("branch", "testpypi-preview")}
    if {(p["type"], p["name"]) for p in policies} != allowed:
        raise ValueError("Unexpected environment deployment ref rules")
