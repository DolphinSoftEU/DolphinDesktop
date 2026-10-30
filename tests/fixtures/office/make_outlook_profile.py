"""Create a dedicated classic-Outlook MAPI profile for the live Outlook tests.

The profile ("DolphinUAT" by default) has exactly one service: a local Personal
Folders data file (``.pst``). No mail account, no server, no network, so the
tests in ``tests/office/test_outlook_live.py`` can create, move and delete
drafts without any real mailbox being involved and without any way to send.

This uses Outlook's MAPI profile-administration API through pywin32, which is
what the Control Panel "Mail" applet uses. It needs classic Outlook installed
(new Outlook has no MAPI). Run it once per Windows user::

    uv run python tests/fixtures/office/make_outlook_profile.py [--profile NAME] [--pst PATH]

Afterwards start classic Outlook once interactively: on its first run it shows
the welcome wizard, where "do not connect to an e-mail account" and "use Outlook
without an email account" finish the setup. From then on COM automation logs on
to the profile without any dialog.

To undo: Control Panel -> Mail -> Show Profiles -> Remove, then delete the PST.
"""

from __future__ import annotations

import argparse
import os
import winreg
from pathlib import Path

import pywintypes
from win32com.mapi import mapi, mapitags

PR_PST_PATH_W = 0x6700001F  # PROP_TAG(PT_UNICODE, 0x6700)
PST_SERVICE = "MSUPST MS"


def _profile_names(admin) -> set[str]:
    rows = mapi.HrQueryAllRows(
        admin.GetProfileTable(0), (mapitags.PR_DISPLAY_NAME_A,), None, None, 0
    )
    names = set()
    for row in rows:
        value = row[0][1]
        names.add(value.decode() if isinstance(value, bytes) else str(value))
    return names


def create_profile(name: str, pst: Path, *, default: bool = True) -> None:
    pst.parent.mkdir(parents=True, exist_ok=True)
    mapi.MAPIInitialize(None)
    try:
        admin = mapi.MAPIAdminProfiles(0)
        if name in _profile_names(admin):
            raise SystemExit(
                f"profile {name!r} already exists; remove it first or pick another name"
            )
        admin.CreateProfile(name, None, 0, 0)
        services = admin.AdminServices(name, None, 0, 0)
        services.CreateMsgService(PST_SERVICE, None, 0, 0)
        rows = mapi.HrQueryAllRows(
            services.GetMsgServiceTable(0),
            (mapitags.PR_SERVICE_UID, mapitags.PR_SERVICE_NAME_A),
            None,
            None,
            0,
        )
        uid = None
        for row in rows:
            tags = dict(row)
            service_name = tags.get(mapitags.PR_SERVICE_NAME_A)
            if service_name in (PST_SERVICE, PST_SERVICE.encode()):
                uid = tags[mapitags.PR_SERVICE_UID]
        if uid is None:
            raise SystemExit("the Personal Folders service was not created")
        services.ConfigureMsgService(
            pywintypes.IID(uid, True),
            0,
            0,
            [(PR_PST_PATH_W, str(pst)), (mapitags.PR_DISPLAY_NAME_W, "Dolphin UAT Store")],
        )
        if default:
            admin.SetDefaultProfile(name, 0)
            # "Always use this profile": never show the profile picker at startup.
            with winreg.CreateKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Exchange\Client\Options"
            ) as key:
                winreg.SetValueEx(key, "PickLogonProfile", 0, winreg.REG_SZ, "0")
    finally:
        mapi.MAPIUninitialize()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--profile", default="DolphinUAT")
    parser.add_argument(
        "--pst",
        default=os.path.join(
            os.environ.get("LOCALAPPDATA", "."), "DolphinDesktop", "uat", "DolphinUAT.pst"
        ),
    )
    parser.add_argument(
        "--no-default", action="store_true", help="do not make it the default profile"
    )
    args = parser.parse_args()
    pst = Path(args.pst).resolve()
    create_profile(args.profile, pst, default=not args.no_default)
    print(f"profile {args.profile!r} created with store {pst}")
    print("now start classic Outlook once and finish its welcome wizard without an e-mail account")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
