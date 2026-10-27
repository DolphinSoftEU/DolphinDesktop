"""KAN-579: select only distributions produced by successful, protected-source CI."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

REQUIRED_JOBS = {
    "Qt agent reproducibility / Qt5 and Qt6",
    "Unit / Python 3.11 / Windows",
    "Unit / Python 3.12 / Windows",
    "Unit / Python 3.13 / Windows",
    "Windows component tests",
    "Linux package install and import",
    "Build package",
    "Security gate (CVE / secrets / SAST) / ubuntu-latest",
    "Security gate (CVE / secrets / SAST) / windows-latest",
    *{
        f"Wheel install / {system} / Python {python}"
        for system in ("ubuntu-latest", "macos-latest", "windows-latest")
        for python in ("3.11", "3.12", "3.13")
    },
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
    if any(type(run.get(key)) is not int or run[key] <= 0 for key in ("id", "run_attempt")):
        raise ValueError("CI run/attempt identifiers must be positive integers")
    jobs = api.pages(f"/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", "jobs")
    passed = {
        job["name"]
        for job in jobs
        if job.get("head_sha") == sha
        and job.get("status") == "completed"
        and job.get("conclusion") == "success"
    }
    if not REQUIRED_JOBS <= passed:
        raise ValueError("Required CI/security/platform jobs did not pass for this SHA")
    artifacts = api.pages(f"/actions/runs/{run['id']}/artifacts", "artifacts")
    name = f"dolphin-desktop-dist-attempt-{run['run_attempt']}"
    artifacts = [a for a in artifacts if a["name"] == name]
    if len(artifacts) != 1:
        raise ValueError("Expected exactly one CI distribution artifact")
    artifact = artifacts[0]
    origin = artifact.get("workflow_run", {})
    if (
        type(artifact.get("id")) is not int
        or artifact["id"] <= 0
        or artifact.get("expired") is not False
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
        api = GitHub(repository, os.environ["GH_TOKEN"])
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid release SHA")
        checked_out = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        if checked_out != sha:
            raise ValueError("Checkout does not match release SHA")
        from validate_release import release_target

        version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"][
            "version"
        ]
        target = os.environ["RELEASE_TARGET"]
        if release_target(version, os.environ["EVENT_NAME"], ref, target) != target:
            raise ValueError("Release target does not match event/ref policy")
        branch = "testpypi-preview" if ref == "refs/heads/testpypi-preview" else "master"
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, f"refs/remotes/origin/{branch}"],
            check=True,
        )
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
