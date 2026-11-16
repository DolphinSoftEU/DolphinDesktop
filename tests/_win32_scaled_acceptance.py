"""Run physical menu acceptance at a verified 125% display scale, without skips."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

from tests._display_scale import scaled_display

REQUIRED = {
    "test_native_menu_click_opens_and_reopens_nested_popups",
    "test_winforms_launch_controls_menu_dialog_and_attach[dpi-unaware]",
    "test_winforms_launch_controls_menu_dialog_and_attach[dpi-aware]",
}


def verify_report(path: Path) -> None:
    """Require all physical-click scenarios to pass without skips."""
    cases = list(ElementTree.parse(path).iter("testcase"))
    if len(cases) != len(REQUIRED) or {case.get("name") for case in cases} != REQUIRED:
        raise RuntimeError("Scaled-menu acceptance is incomplete")
    if any(case.find(tag) is not None for case in cases for tag in ("skipped", "failure", "error")):
        raise RuntimeError("Scaled-menu acceptance requires a real PASS for all three scenarios")


def main() -> int:
    """Provision ephemeral CI scaling, run real menus and retain measured evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/win32-scaled"))
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = output / "results.xml"
    report.unlink(missing_ok=True)
    evidence: dict = {}
    try:
        with scaled_display(125, evidence):
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "tests/component/test_win32_controls_windows.py",
                    "-k",
                    "click_opens or winforms",
                    "-q",
                    f"--junitxml={report}",
                    f"--basetemp={output / 'cases'}",
                    "--dolphin-video=off",
                    "--dolphin-trace=off",
                ],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "DOLPHIN_EXPECT_DISPLAY_SCALE": "125"},
                timeout=480,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(f"Scaled-menu pytest exited with {result.returncode}")
            verify_report(report)
            measurements = [
                path
                for path in (output / "cases").glob("*/display-scale.json")
                if not path.parent.name.endswith("current")
            ]
            if len(measurements) != 3 or any(
                json.loads(path.read_text(encoding="utf-8"))["actual_percent"] != 125
                for path in measurements
            ):
                raise RuntimeError("Missing measured 125% scaling evidence for each AUT")
            evidence["passed"] = True
    except Exception as exc:
        evidence.update(passed=False, error=str(exc))
        print(f"Scaled-menu acceptance failed: {exc}", file=sys.stderr)
        return 1
    finally:
        (output / "display-scale.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
