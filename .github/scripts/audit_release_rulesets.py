"""Independent admin workstation audit; never run with the release GITHUB_TOKEN."""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from release_provenance import GitHub, ruleset_digest


def collect_rulesets(api: GitHub) -> dict:
    evidence = {}
    for entry in api.pages("/rulesets?includes_parents=true"):
        if entry["enforcement"] != "active" or entry["target"] not in {"branch", "tag"}:
            continue
        ruleset = api.get(f"/rulesets/{entry['id']}")
        actors = ruleset.get("bypass_actors")
        if not isinstance(actors, list) or any(not isinstance(actor, dict) for actor in actors):
            raise ValueError("Admin credential did not expose bypass_actors; cannot attest")
        evidence[str(ruleset["id"])] = {
            "policy_sha256": ruleset_digest(ruleset),
            "bypass_actors": actors,
        }
    if not evidence:
        raise ValueError("No active branch/tag rulesets to attest")
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--valid-hours", type=int, default=1, choices=range(1, 25))
    args = parser.parse_args()
    api = GitHub(args.repository, os.environ["RULESETS_ADMIN_TOKEN"])
    issued = int(time.time())
    payload = json.dumps(
        {
            "schema": 1,
            "repository": args.repository,
            "issued_at": issued,
            "expires_at": issued + args.valid_hours * 3600,
            "rulesets": collect_rulesets(api),
        },
        sort_keys=True,
    ).encode()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / "payload.json").write_bytes(payload)
        subprocess.run(
            [
                "openssl",
                "dgst",
                "-sha256",
                "-sign",
                str(args.private_key.resolve()),
                "-out",
                str(root / "signature"),
                str(root / "payload.json"),
            ],
            check=True,
        )
        args.output.write_text(
            json.dumps(
                {
                    "payload": base64.b64encode(payload).decode(),
                    "signature": base64.b64encode((root / "signature").read_bytes()).decode(),
                }
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
