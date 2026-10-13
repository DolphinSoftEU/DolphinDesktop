"""Record and verify the exact CI distributions; keep metadata outside dist/."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path


def distribution_hashes(directory: Path) -> dict[str, str]:
    files = list(directory.iterdir())
    if (
        len(files) != 2
        or sum(p.name.endswith(".whl") for p in files) != 1
        or sum(p.name.endswith(".tar.gz") for p in files) != 1
        or any(not p.is_file() or p.is_symlink() for p in files)
    ):
        raise ValueError("Expected only one wheel and one sdist")
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}


def manifest(directory: Path, sha: str, run_id: str, attempt: str) -> dict:
    return {
        "sha": sha,
        "ci_run_id": run_id,
        "ci_run_attempt": attempt,
        "files": distribution_hashes(directory),
    }


def verify(directory: Path, metadata: Path, sha: str, run_id: str, attempt: str) -> None:
    if json.loads(metadata.read_text(encoding="utf-8")) != manifest(
        directory, sha, run_id, attempt
    ):
        raise ValueError("Distribution hash or CI source identity mismatch")


def main() -> int:
    try:
        directory = Path("release-artifact/dist")
        metadata = Path("release-artifact/provenance.json")
        sha = os.environ["RELEASE_SHA"]
        run_id = os.environ["CI_RUN_ID"]
        attempt = os.environ["CI_RUN_ATTEMPT"]
        if sys.argv[1:] == ["create"]:
            metadata.write_text(
                json.dumps(manifest(directory, sha, run_id, attempt), indent=2) + "\n",
                encoding="utf-8",
            )
        elif sys.argv[1:] == ["verify"]:
            expected = os.environ.get("MANIFEST_SHA256")
            if expected and hashlib.sha256(metadata.read_bytes()).hexdigest() != expected:
                raise ValueError("Manifest SHA256 mismatch")
            verify(directory, metadata, sha, run_id, attempt)
            if os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
                    digest = hashlib.sha256(metadata.read_bytes()).hexdigest()
                    output.write(f"manifest_sha256={digest}\n")
        else:
            raise ValueError("Expected create or verify")
    except (ValueError, KeyError, OSError) as error:
        print(f"Distribution verification rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
