"""Audit native protection without publication or manual ruleset evidence."""

from __future__ import annotations

import os
import sys

from release_policy import (
    PolicyStatus,
    validate_branch,
    validate_environment,
    validate_review,
    validate_tag,
)
from release_provenance import GitHub


def audit(api, repository: str, sha: str, ref: str, target: str) -> PolicyStatus:
    branch = "testpypi-preview" if ref == "refs/heads/testpypi-preview" else "master"
    branch_status = validate_branch(api, branch)
    if target == "pypi" or branch == "master":
        validate_review(api, repository, sha, branch)
    tag_status = validate_tag(api, ref)
    validate_environment(api, target)
    if PolicyStatus.UNKNOWN in (branch_status, tag_status):
        return PolicyStatus.UNKNOWN
    return PolicyStatus.VERIFIED


def report(status: PolicyStatus) -> None:
    message = f"Native release policy status: {status.value}."
    if status == PolicyStatus.UNKNOWN:
        message += (
            " Bypass actors are hidden; authorization is NOT verified."
            " GitHub enforces native rules; complete the one-time administrator"
            " configuration check before acceptance."
        )
    print(message)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            output.write(f"policy_status={status.value}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(message + "\n")


def main() -> int:
    try:
        repository = os.environ["GITHUB_REPOSITORY"]
        status = audit(
            GitHub(repository, os.environ["GH_TOKEN"]),
            repository,
            os.environ["RELEASE_SHA"],
            os.environ["RELEASE_REF"],
            os.environ["RELEASE_TARGET"],
        )
        report(status)
    except (ValueError, KeyError, TypeError, OSError) as error:
        report(PolicyStatus.INVALID)
        print(f"Release policy preflight rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
