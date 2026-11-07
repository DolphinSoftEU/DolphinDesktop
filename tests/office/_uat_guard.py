"""Opt-in safety guard shared by the destructive live-Outlook suites.

These suites create, move and permanently delete Outlook items. They run
against whatever MAPI profile is the machine default, so before any mutation
they must *prove* that profile is the dedicated, disposable UAT profile from
``tests/fixtures/office/make_outlook_profile.py`` and nothing else: a single
local ``.pst`` data file, no mail account, no server.

A display-name match alone is not proof -- any ordinary profile whose default
Drafts happens to point at a PST with that name would pass. ``verify_dedicated_
store`` therefore also requires:

* ``Accounts.Count == 0``  -- no mail account can send or reach a real mailbox;
* ``Stores.Count == 1``    -- exactly the one disposable PST is mounted;
* every folder that will be mutated (drafts, deleted, calendar) is present in
  the facts -- a missing one is a violation, never skipped -- and lives in that
  same single store, matched by ``StoreID`` as well as by name;
* that store is a ``.pst`` file on a local disk: an absolute drive-letter path
  whose drive is fixed, removable or a RAM disk -- never a UNC, device or
  mapped network-drive path;
* optionally, the active ``CurrentProfileName`` equals an explicitly configured
  expected name (``DOLPHIN_OUTLOOK_UAT_PROFILE``), and the PST is the explicitly
  configured file (``DOLPHIN_OUTLOOK_UAT_PST``), when the operator sets them.

The check is split into a pure ``verify_dedicated_store`` (primitives in, raises
on any violation) so it can be unit-tested without Outlook, and a thin
``assert_dedicated_store`` that reads those primitives off a live COM session.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any

UAT_ENV = "DOLPHIN_OUTLOOK_UAT"
UAT_PROFILE_ENV = "DOLPHIN_OUTLOOK_UAT_PROFILE"
UAT_PST_ENV = "DOLPHIN_OUTLOOK_UAT_PST"

# GetDriveTypeW results accepted as local: removable (2), fixed (3), RAM disk
# (6). Remote (4), CD-ROM (5), unknown (0) and "no root dir" (1) are refused.
_LOCAL_DRIVE_TYPES = frozenset({2, 3, 6})
_DRIVE_PATH = re.compile(r"^[A-Za-z]:\\")

# Folders the suites create/move/delete items in; all must be in the one PST.
GUARDED_FOLDERS = ("drafts", "deleted", "calendar")


def uat_store_name() -> str | None:
    """The display name of the dedicated test store the operator opted in to."""
    name = os.environ.get(UAT_ENV, "").strip()
    return name or None


def uat_profile_name() -> str | None:
    """Optional expected MAPI profile name; enforced only when the operator sets it."""
    name = os.environ.get(UAT_PROFILE_ENV, "").strip()
    return name or None


def uat_pst_path() -> str | None:
    """Optional expected PST file; enforced only when the operator sets it."""
    path = os.environ.get(UAT_PST_ENV, "").strip()
    return path or None


def _drive_type(root: str) -> int:
    """``GetDriveTypeW`` for a drive root such as ``C:\\``; 0 (unknown) on failure."""
    try:
        import ctypes

        return int(ctypes.windll.kernel32.GetDriveTypeW(root))
    except Exception:
        return 0


def _normalized(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.replace("/", "\\")))


def local_pst_problem(path: str, *, drive_type: Callable[[str], int] = _drive_type) -> str | None:
    r"""Why *path* is not a ``.pst`` on a local disk, or ``None`` when it is.

    Accepted is only an absolute ``X:\...`` path whose drive reports as local
    (fixed, removable or RAM disk). UNC and device paths (``\\server\share``,
    ``\\?\UNC\...``, ``\\.\...``) and drives mapped to a network share are refused.
    """
    windows_path = path.replace("/", "\\")
    if not windows_path.lower().endswith(".pst"):
        return f"store path {path!r} is not a .pst file"
    if windows_path.startswith("\\\\"):
        return f"store path {path!r} is a UNC or device path, not a local file"
    if not _DRIVE_PATH.match(windows_path):
        return f"store path {path!r} is not an absolute local drive path"
    kind = drive_type(windows_path[:3])
    if kind not in _LOCAL_DRIVE_TYPES:
        return f"store path {path!r} is on a drive that is not local (drive type {kind})"
    return None


def verify_dedicated_store(
    *,
    expected_name: str,
    expected_profile: str | None,
    profile_name: str,
    accounts_count: int,
    stores_count: int,
    folders: dict[str, tuple[str, str, str]],
    expected_pst: str | None = None,
    drive_type: Callable[[str], int] = _drive_type,
) -> None:
    """Raise ``AssertionError`` unless everything proves the disposable UAT store.

    ``folders`` maps each guarded folder key to ``(display_name, file_path,
    store_id)``; every key of ``GUARDED_FOLDERS`` must be present -- a missing
    one is a violation, not something to skip. Unknown counts should be passed
    as a negative number so the guard fails closed rather than mistaking "could
    not read" for "zero". ``expected_pst``, when given, is the one PST file the
    store must be.
    """
    problems: list[str] = []

    if expected_profile is not None and profile_name != expected_profile:
        problems.append(f"active MAPI profile is {profile_name!r}, expected {expected_profile!r}")
    if accounts_count != 0:
        problems.append(
            f"profile exposes {accounts_count} mail account(s); the UAT profile must have none"
        )
    if stores_count != 1:
        problems.append(
            f"profile exposes {stores_count} store(s); the UAT profile must have exactly one PST"
        )

    allowed_store_id: str | None = None
    for key in GUARDED_FOLDERS:
        if key not in folders:
            problems.append(f"{key} folder facts are missing, so its store cannot be verified")
            continue
        name, path, store_id = folders[key]
        if name != expected_name:
            problems.append(f"{key} folder is in store {name!r}, expected {expected_name!r}")
        path_problem = local_pst_problem(path, drive_type=drive_type)
        if path_problem is not None:
            problems.append(f"{key} folder {path_problem}")
        elif expected_pst is not None and _normalized(path) != _normalized(expected_pst):
            problems.append(
                f"{key} folder store is {path!r}, not the configured {UAT_PST_ENV} {expected_pst!r}"
            )
        if allowed_store_id is None:
            allowed_store_id = store_id
        elif store_id != allowed_store_id:
            problems.append(
                f"{key} folder lives in a different store than the others; drafts, deleted "
                "and calendar must all share the one UAT PST"
            )

    if problems:
        raise AssertionError(
            "refusing to run destructive Outlook tests -- "
            + "; ".join(problems)
            + f". Point {UAT_ENV} at the dedicated, disposable PST (a local store with no mail "
            "account, see tests/fixtures/office/make_outlook_profile.py) and make it the "
            "default profile."
        )


def _collection_count(namespace: Any, attr: str) -> int:
    """Count a MAPI namespace collection, or -1 when it cannot be read (fail closed)."""
    try:
        collection = getattr(namespace, attr)
        return int(collection.Count)
    except Exception:
        return -1


def assert_dedicated_store(app: Any) -> None:
    """Read the live COM facts for *app* and verify them before any mutation."""
    expected = uat_store_name()
    assert expected, f"{UAT_ENV} must be set (checked in _require_outlook)"
    namespace = app._namespace
    folders: dict[str, tuple[str, str, str]] = {}
    for key in GUARDED_FOLDERS:
        store = app.default_folder(key)._com.Store
        folders[key] = (
            str(store.DisplayName),
            str(getattr(store, "FilePath", "") or ""),
            str(store.StoreID),
        )
    verify_dedicated_store(
        expected_name=expected,
        expected_profile=uat_profile_name(),
        profile_name=str(getattr(namespace, "CurrentProfileName", "") or ""),
        accounts_count=_collection_count(namespace, "Accounts"),
        stores_count=_collection_count(namespace, "Stores"),
        folders=folders,
        expected_pst=uat_pst_path(),
    )
