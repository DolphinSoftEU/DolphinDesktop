"""Download the selected immutable CI artifact and enforce its API digest."""

from __future__ import annotations

import hashlib
import io
import os
import re
import urllib.request
import zipfile
from pathlib import Path


class NoAuthRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            if not newurl.startswith("https://"):
                raise ValueError("Artifact redirect must use HTTPS")
            redirected.remove_header("Authorization")
        return redirected


def unpack(data: bytes, digest: str, directory: Path) -> None:
    if "sha256:" + hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("CI artifact archive SHA256 mismatch")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(names) != 3 or len(set(names)) != 3 or "provenance.json" not in names:
            raise ValueError("Unexpected CI artifact archive entries")
        for name in names:
            if name != "provenance.json" and not re.fullmatch(
                r"dist/[A-Za-z0-9_.+-]+\.(whl|tar\.gz)", name
            ):
                raise ValueError("Unsafe CI artifact path")
        for name in names:
            destination = directory / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(name))


def main() -> None:
    repository = os.environ["GITHUB_REPOSITORY"]
    artifact_id = int(os.environ["CI_ARTIFACT_ID"])
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/actions/artifacts/{artifact_id}/zip",
        headers={
            "Authorization": f"Bearer {os.environ['GH_TOKEN']}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.build_opener(NoAuthRedirect()).open(request, timeout=60) as response:
        data = response.read()
    unpack(data, os.environ["CI_ARTIFACT_DIGEST"], Path("release-artifact"))


if __name__ == "__main__":
    main()
