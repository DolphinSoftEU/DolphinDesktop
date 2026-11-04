"""Materialize the exact Microsoft compiler, CRT and Windows SDK for the Qt DLLs."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

LOCK = Path(__file__).resolve().parents[1] / "toolchains" / "msvc.json"


def extract_archive(
    archive: Path, destination: Path, map_path: Callable[[PurePosixPath], PurePosixPath | None]
) -> None:
    with zipfile.ZipFile(archive) as payload:
        for entry in payload.infolist():
            # ZipInfo normalizes Windows separators and strips NUL suffixes;
            # validate the original archive name before using its parsed form.
            raw_name = entry.orig_filename
            path = PurePosixPath(raw_name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or any(character in raw_name for character in ("\\", ":", "\0"))
            ):
                raise ValueError(f"Unsafe toolchain archive path: {entry.filename}")
            relative = map_path(path)
            if relative is None or entry.is_dir():
                continue
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Toolchain archive contains a symbolic link")
            target = destination.joinpath(*relative.parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise ValueError("Toolchain archive escapes the installation directory")
            target.parent.mkdir(parents=True, exist_ok=True)
            with payload.open(entry) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def extract_payload(archive: Path, destination: Path, tools_directory: str) -> None:
    prefix = f"Contents/VC/Tools/MSVC/{tools_directory}/"
    extract_archive(
        archive,
        destination,
        lambda p: PurePosixPath(*p.parts[1:]) if p.as_posix().startswith(prefix) else None,
    )


def extract_sdk_payload(archive: Path, destination: Path, sdk_version: str) -> None:
    def map_path(path: PurePosixPath) -> PurePosixPath | None:
        parts = path.parts
        if parts[:3] == ("c", "Include", sdk_version):
            return PurePosixPath("WindowsSDK", *parts[1:])
        if parts[:4] == ("c", "bin", sdk_version, "x64"):
            return PurePosixPath("WindowsSDK", *parts[1:])
        if len(parts) >= 3 and parts[0] == "c" and parts[1] in {"um", "ucrt"} and parts[2] == "x64":
            return PurePosixPath("WindowsSDK", "Lib", sdk_version, *parts[1:])
        return None

    extract_archive(archive, destination, map_path)


def verify_payload(path: Path, record: dict) -> None:
    if path.stat().st_size != record["size"]:
        raise ValueError(f"Incorrect size for {record['id']}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != record["sha256"]:
        raise ValueError(f"Incorrect SHA-256 for {record['id']}")


def install(destination: Path, lock: dict) -> None:
    destination = destination.resolve()
    if destination.exists():
        raise ValueError(f"Choose a fresh toolchain directory: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Publish only a complete installation. Partial downloads/extraction are
    # removed on failure; the runner's installed Visual Studio is untouched.
    with tempfile.TemporaryDirectory(prefix="dolphin-msvc-", dir=destination.parent) as scratch:
        scratch_path = Path(scratch)
        installation = scratch_path / "installation"
        installation.mkdir()
        packages = [(record, "msvc") for record in lock["packages"]]
        packages += [(record, "sdk") for record in lock.get("windows_sdk_packages", [])]
        for number, (record, kind) in enumerate(packages):
            url = urllib.parse.urlsplit(record["url"])
            host = "api.nuget.org" if kind == "sdk" else "download.visualstudio.microsoft.com"
            if url.scheme != "https" or url.netloc != host:
                raise ValueError(f"Toolchain payload must come from Microsoft's HTTPS host: {host}")
            archive = scratch_path / f"{number}.vsix"
            with urllib.request.urlopen(record["url"], timeout=120) as source:
                with archive.open("wb") as output:
                    shutil.copyfileobj(source, output)
            verify_payload(archive, record)
            if kind == "sdk":
                extract_sdk_payload(archive, installation, lock["windows_sdk"])
            else:
                extract_payload(archive, installation, lock["tools_directory"])
            print(f"Verified {record['id']} {record['version']}", flush=True)
        tools = installation / "VC" / "Tools" / "MSVC" / lock["tools_directory"]
        for relative in (
            "bin/Hostx64/x64/cl.exe",
            "bin/Hostx64/x64/link.exe",
            "bin/Hostx64/x64/dumpbin.exe",
            "include/vector",
            "lib/x64/msvcrt.lib",
        ):
            if not (tools / relative).is_file():
                raise ValueError(f"Incomplete MSVC payloads: {relative}")
        if lock.get("windows_sdk_packages"):
            sdk = installation / "WindowsSDK"
            version = lock["windows_sdk"]
            for relative in (
                f"Include/{version}/um/Windows.h",
                f"Include/{version}/shared/sdkddkver.h",
                f"Include/{version}/ucrt/corecrt.h",
                f"Lib/{version}/um/x64/kernel32.lib",
                f"Lib/{version}/ucrt/x64/ucrt.lib",
                f"bin/{version}/x64/rc.exe",
                f"bin/{version}/x64/mt.exe",
            ):
                if not (sdk / relative).is_file():
                    raise ValueError(f"Incomplete Windows SDK payloads: {relative}")
        (installation / "toolchain.json").write_text(json.dumps(lock, indent=2) + "\n")
        installation.rename(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    install(args.destination, json.loads(LOCK.read_text(encoding="utf-8")))
