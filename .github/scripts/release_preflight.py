"""Audit native protection without publication or manual ruleset evidence."""

from __future__ import annotations

import os
import sys

from release_policy import validate_branch, validate_environment, validate_review, validate_tag
from release_provenance import GitHub


def audit(api, repository: str, sha: str, ref: str, target: str) -> None:
    branch = "testpypi-preview" if ref == "refs/heads/testpypi-preview" else "master"
    validate_branch(api, branch)
    if target == "pypi" or branch == "master":
        validate_review(api, repository, sha, branch)
    validate_tag(api, ref)
    validate_environment(api, target)


def main() -> int:
    try:
        repository = os.environ["GITHUB_REPOSITORY"]
        audit(
            GitHub(repository, os.environ["GH_TOKEN"]),
            repository,
            os.environ["RELEASE_SHA"],
            os.environ["RELEASE_REF"],
            os.environ["RELEASE_TARGET"],
        )
        print("Native release policy preflight passed (hidden bypasses are enforced by GitHub).")
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(f"Release policy preflight rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
