"""Unit tests for the destructive-Outlook opt-in guard (no Outlook required).

These exercise the pure ``verify_dedicated_store`` with fabricated MAPI facts,
so they run everywhere (including CI without Outlook) and prove the guard
refuses anything that is not the dedicated, disposable UAT profile: a profile
with a mail account, more than one store, a wrongly-named or non-PST store, a
folder living in a different store, or the wrong active profile name.
"""

from __future__ import annotations

import pytest

from tests.office._uat_guard import GUARDED_FOLDERS, verify_dedicated_store

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
    with pytest.raises(AssertionError, match=r"not a local \.pst"):
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
