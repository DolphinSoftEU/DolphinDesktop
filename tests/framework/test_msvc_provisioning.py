"""The CI compiler must come from immutable, verified Microsoft payloads."""

from __future__ import annotations

import hashlib
import io
import json
import runpy
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
module = runpy.run_path(str(ROOT / ".github/scripts/install_msvc.py"))
install = module["install"]
extract_payload = module["extract_payload"]
verify_payload = module["verify_payload"]


def make_archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, contents in files.items():
            entry = zipfile.ZipInfo()
            entry.filename = name  # Preserve backslashes even on Windows.
            archive.writestr(entry, contents)
    return buffer.getvalue()


def fixture_lock(data: bytes) -> dict:
    return {
        "tools_directory": "14.50.35717",
        "packages": [
            {
                "id": "fixture.compiler",
                "version": "14.50.35726",
                "url": "https://download.visualstudio.microsoft.com/fixture.vsix",
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        ],
    }


def compiler_archive() -> bytes:
    prefix = "Contents/VC/Tools/MSVC/14.50.35717/"
    return make_archive(
        {
            prefix + name: b"verified fixture"
            for name in (
                "bin/Hostx64/x64/cl.exe",
                "bin/Hostx64/x64/link.exe",
                "bin/Hostx64/x64/dumpbin.exe",
                "include/vector",
                "lib/x64/msvcrt.lib",
            )
        }
    )


@pytest.mark.parametrize("damage", ["same_size_tamper", "truncate"])
def test_modified_payload_is_rejected(tmp_path, damage):
    original = compiler_archive()
    modified = original[:-1] if damage == "truncate" else bytes([original[0] ^ 1]) + original[1:]
    path = tmp_path / "payload.vsix"
    path.write_bytes(modified)
    with pytest.raises(ValueError, match="Incorrect"):
        verify_payload(path, fixture_lock(original)["packages"][0])


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "Contents/../escape", "C:/escape", "Contents\\escape"]
)
def test_archive_paths_cannot_escape_toolchain_directory(tmp_path, name):
    path = tmp_path / "payload.vsix"
    path.write_bytes(make_archive({name: b"untrusted"}))
    with pytest.raises(ValueError, match="Unsafe"):
        extract_payload(path, tmp_path / "installation", "14.50.35717")


def test_complete_verified_payload_is_published(tmp_path, monkeypatch):
    data = compiler_archive()
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: io.BytesIO(data))
    destination = tmp_path / "toolchain"
    lock = fixture_lock(data)
    install(destination, lock)
    assert json.loads((destination / "toolchain.json").read_text()) == lock
    assert (destination / "VC/Tools/MSVC/14.50.35717/bin/Hostx64/x64/cl.exe").read_bytes()
    assert list(tmp_path.iterdir()) == [destination]
    with pytest.raises(ValueError, match="fresh"):
        install(destination, lock)


@pytest.mark.parametrize("failure", ["checksum", "incomplete", "network", "untrusted_host"])
def test_failed_install_does_not_publish_or_leave_temporary_files(tmp_path, monkeypatch, failure):
    data = compiler_archive() if failure != "incomplete" else make_archive({"metadata": b"empty"})
    lock = fixture_lock(data)
    if failure == "checksum":
        lock["packages"][0]["sha256"] = hashlib.sha256(b"different").hexdigest()
    if failure == "untrusted_host":
        lock["packages"][0]["url"] = "https://example.invalid/compiler.vsix"

    def download(*args, **kwargs):
        if failure == "network":
            raise OSError("controlled download failure")
        return io.BytesIO(data)

    monkeypatch.setattr("urllib.request.urlopen", download)
    with pytest.raises((ValueError, OSError)):
        install(tmp_path / "toolchain", lock)
    assert not list(tmp_path.iterdir())
