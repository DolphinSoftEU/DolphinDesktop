"""The CI compiler must come from immutable, verified Microsoft payloads."""

from __future__ import annotations

import hashlib
import io
import json
import os
import runpy
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
module = runpy.run_path(str(ROOT / ".github/scripts/install_msvc.py"))
install = module["install"]
extract_payload = module["extract_payload"]
verify_payload = module["verify_payload"]
extract_sdk_payload = module["extract_sdk_payload"]


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


def sdk_archive(*, complete=True) -> bytes:
    version = "10.0.26100.0"
    files = {
        f"c/Include/{version}/um/Windows.h": b"headers-7705",
        f"c/Include/{version}/shared/sdkddkver.h": b"shared",
        f"c/Include/{version}/ucrt/corecrt.h": b"ucrt",
        f"c/bin/{version}/x64/rc.exe": b"rc-7705",
        f"c/bin/{version}/x64/mt.exe": b"mt-7705",
        "c/um/x64/kernel32.lib": b"import-library-7705",
    }
    if complete:
        files["c/ucrt/x64/ucrt.lib"] = b"ucrt-library"
    return make_archive(files)


@pytest.mark.parametrize("failure", [None, "incomplete", "tamper", "untrusted_host"])
def test_sdk_is_verified_and_published_with_compiler_or_everything_is_rolled_back(
    tmp_path, monkeypatch, failure
):
    compiler = compiler_archive()
    sdk = sdk_archive(complete=failure != "incomplete")
    lock = fixture_lock(compiler)
    lock["windows_sdk"] = "10.0.26100.0"
    lock["windows_sdk_package_version"] = "10.0.26100.7705"
    lock["windows_sdk_packages"] = [
        {
            "id": "fixture.sdk",
            "version": "10.0.26100.7705",
            "url": "https://api.nuget.org/fixture.nupkg",
            "size": len(sdk),
            "sha256": hashlib.sha256(sdk).hexdigest(),
        }
    ]
    if failure == "tamper":
        sdk = bytes([sdk[0] ^ 1]) + sdk[1:]
    if failure == "untrusted_host":
        lock["windows_sdk_packages"][0]["url"] = "https://example.invalid/fixture.nupkg"
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda url, **kw: io.BytesIO(sdk if url.endswith("nupkg") else compiler),
    )
    destination = tmp_path / "toolchain"
    if failure:
        with pytest.raises(ValueError):
            install(destination, lock)
        assert not list(tmp_path.iterdir())
    else:
        install(destination, lock)
        assert (
            destination / "WindowsSDK/Include/10.0.26100.0/um/Windows.h"
        ).read_bytes() == b"headers-7705"
        assert (
            destination / "WindowsSDK/Lib/10.0.26100.0/um/x64/kernel32.lib"
        ).read_bytes() == b"import-library-7705"
        assert (destination / "WindowsSDK/bin/10.0.26100.0/x64/rc.exe").read_bytes() == b"rc-7705"
        assert json.loads((destination / "toolchain.json").read_text()) == lock
        assert list(tmp_path.iterdir()) == [destination]


def test_sdk_archive_cannot_escape_installation(tmp_path):
    path = tmp_path / "payload.nupkg"
    path.write_bytes(make_archive({"c/Include/10.0.26100.0/../../escape": b"untrusted"}))
    with pytest.raises(ValueError, match="Unsafe"):
        extract_sdk_payload(path, tmp_path / "installation", "10.0.26100.0")


@pytest.mark.skipif(os.name != "nt", reason="Windows toolchain environment")
def test_initializer_ignores_registry_sdk_and_ambient_compiler_paths(tmp_path):
    pwsh = shutil.which("pwsh")
    assert pwsh
    script = tmp_path / "environment.ps1"
    script.write_text(
        r"""
param([string]$Repository, [string]$Sandbox)
$ErrorActionPreference = 'Stop'
$lock = Get-Content (Join-Path $Repository '.github/toolchains/msvc.json') -Raw | ConvertFrom-Json
$lock | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $Sandbox 'toolchain.json')
$tools = Join-Path $Sandbox "VC/Tools/MSVC/$($lock.tools_directory)"
$expectedSdk = Join-Path $Sandbox 'WindowsSDK'
New-Item -ItemType Directory -Path $tools -Force | Out-Null
foreach ($relative in @('Include/10.0.26100.0/ucrt', 'Include/10.0.26100.0/shared',
                        'Include/10.0.26100.0/um', 'Lib/10.0.26100.0/ucrt/x64',
                        'Lib/10.0.26100.0/um/x64', 'bin/10.0.26100.0/x64')) {
    New-Item -ItemType Directory -Path (Join-Path $expectedSdk $relative) -Force | Out-Null
}
function Get-ItemProperty { throw 'Registry SDK must not be read' }
function cl.exe { 'MSVC 19.50.35726' }
$env:CC = $env:CXX = 'Z:/ambient/compiler.exe'
$env:INCLUDE = $env:LIB = $env:LIBPATH = 'Z:/ambient'
$env:WindowsSdkDir = 'Z:/registry-sdk'
. (Join-Path $Repository '.github/scripts/enter_msvc.ps1') -ToolchainRoot $Sandbox
if ($env:CXX -ne (Join-Path $tools 'bin/Hostx64/x64/cl.exe') -or $env:CC -ne $env:CXX) {
    throw 'Ambient compiler selected'
}
if ($env:WindowsSdkDir.TrimEnd('\') -ne $expectedSdk -or
    $env:DOLPHIN_WINDOWS_SDK_PACKAGE_VERSION -ne $lock.windows_sdk_package_version -or
    $env:INCLUDE.Contains('Z:') -or $env:LIB.Contains('Z:') -or $env:LIBPATH.Contains('Z:')) {
    throw 'Ambient SDK selected'
}
'PASS'
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            pwsh,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(script),
            "-Repository",
            str(ROOT),
            "-Sandbox",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout
