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
* every folder that will be mutated (drafts, deleted, calendar) lives in that
  same single store, matched by ``StoreID`` as well as by name and ``.pst`` path;
* optionally, the active ``CurrentProfileName`` equals an explicitly configured
  expected name (``DOLPHIN_OUTLOOK_UAT_PROFILE``) when the operator sets it.

The check is split into a pure ``verify_dedicated_store`` (primitives in, raises
on any violation) so it can be unit-tested without Outlook, and a thin
``assert_dedicated_store`` that reads those primitives off a live COM session.
"""

from __future__ import annotations

import os
from typing import Any

UAT_ENV = "DOLPHIN_OUTLOOK_UAT"
UAT_PROFILE_ENV = "DOLPHIN_OUTLOOK_UAT_PROFILE"

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


def verify_dedicated_store(
    *,
    expected_name: str,
    expected_profile: str | None,
    profile_name: str,
    accounts_count: int,
    stores_count: int,
    folders: dict[str, tuple[str, str, str]],
) -> None:
    """Raise ``AssertionError`` unless everything proves the disposable UAT store.

    ``folders`` maps each guarded folder key to ``(display_name, file_path,
    store_id)``. Unknown counts should be passed as a negative number so the
    guard fails closed rather than mistaking "could not read" for "zero".
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
            continue
        name, path, store_id = folders[key]
        if name != expected_name:
            problems.append(f"{key} folder is in store {name!r}, expected {expected_name!r}")
        if not path.lower().endswith(".pst"):
            problems.append(f"{key} folder store path {path!r} is not a local .pst file")
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
    )
