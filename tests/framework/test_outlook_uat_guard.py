"""Unit tests for the destructive-Outlook opt-in guard (no Outlook required).

These exercise the pure ``verify_dedicated_store`` with fabricated MAPI facts,
so they run everywhere (including CI without Outlook) and prove the guard
refuses anything that is not the dedicated, disposable UAT profile: a profile
with a mail account, more than one store, a wrongly-named or non-PST store, a
folder living in a different store, or the wrong active profile name.
"""

from __future__ import annotations

import pytest

from tests.office._uat_guard import GUARDED_FOLDERS, local_pst_problem, verify_dedicated_store

pytestmark = pytest.mark.unit

_NAME = "Dolphin UAT Store"
_PST = r"C:\Users\uat\AppData\Local\DolphinDesktop\uat\DolphinUAT.pst"
_STORE_ID = "00000000DEADBEEF"


def _folders(**overrides: tuple[str, str, str]) -> dict[str, tuple[str, str, str]]:
    folders = dict.fromkeys(GUARDED_FOLDERS, (_NAME, _PST, _STORE_ID))
    folders.update(overrides)
    return folders


def _verify(**overrides: object) -> None:
    kwargs: dict[str, object] = {
        "expected_name": _NAME,
        "expected_profile": None,
        "profile_name": "DolphinUAT",
        "accounts_count": 0,
        "stores_count": 1,
        "folders": _folders(),
        # Deterministic: every drive is a local fixed disk unless a test says otherwise.
        "drive_type": lambda root: 3,
    }
    kwargs.update(overrides)
    verify_dedicated_store(**kwargs)  # type: ignore[arg-type]


def test_accepts_a_clean_single_pst_profile_without_accounts():
    _verify()  # must not raise


def test_rejects_a_profile_that_has_a_mail_account():
    with pytest.raises(AssertionError, match="mail account"):
        _verify(accounts_count=1)


def test_rejects_a_profile_with_more_than_one_store():
    with pytest.raises(AssertionError, match="store"):
        _verify(stores_count=2)


def test_rejects_when_a_count_could_not_be_read():
    # _collection_count returns -1 on failure; the guard must fail closed.
    with pytest.raises(AssertionError):
        _verify(accounts_count=-1)
    with pytest.raises(AssertionError):
        _verify(stores_count=-1)


def test_rejects_a_store_whose_display_name_is_not_the_opted_in_one():
    with pytest.raises(AssertionError, match="expected"):
        _verify(folders=_folders(drafts=("Personal mailbox", _PST, _STORE_ID)))


def test_rejects_a_store_path_that_is_not_a_pst():
    bad = r"C:\Users\uat\AppData\Local\Microsoft\Outlook\real.ost"
    with pytest.raises(AssertionError, match=r"not a \.pst file"):
        _verify(folders=_folders(drafts=(_NAME, bad, _STORE_ID)))


def test_rejects_when_a_guarded_folder_lives_in_a_different_store():
    with pytest.raises(AssertionError, match="different store"):
        _verify(folders=_folders(calendar=(_NAME, _PST, "00000000FEEDFACE")))


def test_enforces_the_expected_profile_name_only_when_configured():
    # Not configured: any profile name is accepted.
    _verify(expected_profile=None, profile_name="Whatever")
    # Configured and mismatched: refused.
    with pytest.raises(AssertionError, match="active MAPI profile"):
        _verify(expected_profile="DolphinUAT", profile_name="Outlook")
    # Configured and matching: accepted.
    _verify(expected_profile="DolphinUAT", profile_name="DolphinUAT")


# ---------------------------------------------------------------------------
# Incomplete folder facts: fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", GUARDED_FOLDERS)
def test_rejects_when_a_guarded_folder_is_missing_from_the_facts(missing):
    folders = _folders()
    del folders[missing]
    with pytest.raises(AssertionError, match=f"{missing} folder facts are missing"):
        _verify(folders=folders)


def test_rejects_an_empty_folder_map_even_with_clean_counts():
    with pytest.raises(AssertionError, match="facts are missing"):
        _verify(folders={}, accounts_count=0, stores_count=1)


# ---------------------------------------------------------------------------
# Local PST only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        r"\server\share\DolphinUAT.pst",
        "//server/share/DolphinUAT.pst",
        r"\?\UNC\server\share\DolphinUAT.pst",
        r"\?\C:\uat\DolphinUAT.pst",
        r"\.\C:\uat\DolphinUAT.pst",
    ],
)
def test_rejects_unc_and_device_paths(path):
    with pytest.raises(AssertionError, match="UNC or device path"):
        _verify(folders=dict.fromkeys(GUARDED_FOLDERS, (_NAME, path, _STORE_ID)))


@pytest.mark.parametrize("path", [r"uat\DolphinUAT.pst", r"C:DolphinUAT.pst", "DolphinUAT.pst"])
def test_rejects_paths_that_are_not_absolute_drive_paths(path):
    with pytest.raises(AssertionError, match="not an absolute local drive path"):
        _verify(folders=dict.fromkeys(GUARDED_FOLDERS, (_NAME, path, _STORE_ID)))


@pytest.mark.parametrize("kind", [0, 1, 4, 5])  # unknown, no root, remote, CD-ROM
def test_rejects_a_pst_on_a_drive_that_is_not_local(kind):
    mapped = r"Z:\uat\DolphinUAT.pst"
    with pytest.raises(AssertionError, match=rf"not local \(drive type {kind}\)"):
        _verify(
            folders=dict.fromkeys(GUARDED_FOLDERS, (_NAME, mapped, _STORE_ID)),
            drive_type=lambda root: kind,
        )


@pytest.mark.parametrize("kind", [2, 3, 6])  # removable, fixed, RAM disk
def test_accepts_a_pst_on_a_local_drive(kind):
    assert local_pst_problem(_PST, drive_type=lambda root: kind) is None


def test_the_drive_type_is_asked_for_the_drive_root():
    roots: list[str] = []
    local_pst_problem(r"D:\uat\DolphinUAT.pst", drive_type=lambda root: roots.append(root) or 3)
    assert roots == ["D:\\"]


# ---------------------------------------------------------------------------
# Explicitly configured PST file
# ---------------------------------------------------------------------------


def test_accepts_the_configured_pst_regardless_of_case_and_separators():
    configured = _PST.upper().replace("\\", "/")
    _verify(expected_pst=configured)


def test_rejects_a_local_pst_that_is_not_the_configured_one():
    other = r"C:\Users\uat\Documents\Outlook Files\personal.pst"
    with pytest.raises(AssertionError, match="DOLPHIN_OUTLOOK_UAT_PST"):
        _verify(
            folders=dict.fromkeys(GUARDED_FOLDERS, (_NAME, other, _STORE_ID)),
            expected_pst=_PST,
        )


def test_live_guard_passes_the_configured_pst_to_the_check(monkeypatch):
    from types import SimpleNamespace

    from tests.office import _uat_guard

    seen: dict[str, object] = {}
    monkeypatch.setenv(_uat_guard.UAT_ENV, _NAME)
    monkeypatch.setenv(_uat_guard.UAT_PST_ENV, _PST)
    monkeypatch.setattr(_uat_guard, "verify_dedicated_store", lambda **kw: seen.update(kw))
    store = SimpleNamespace(DisplayName=_NAME, FilePath=_PST, StoreID=_STORE_ID)
    namespace = SimpleNamespace(
        CurrentProfileName="DolphinUAT",
        Accounts=SimpleNamespace(Count=0),
        Stores=SimpleNamespace(Count=1),
    )
    app = SimpleNamespace(
        _namespace=namespace,
        default_folder=lambda key: SimpleNamespace(_com=SimpleNamespace(Store=store)),
    )
    _uat_guard.assert_dedicated_store(app)
    assert seen["expected_pst"] == _PST
    assert set(seen["folders"]) == set(GUARDED_FOLDERS)
