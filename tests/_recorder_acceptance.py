"""Run recorder acceptance on an interactive Windows desktop; skips never pass."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

REQUIRED_TESTS = {
    "test_real_hook_installation_and_cleanup",
    "test_real_hook_delayed_password_text_transitions",
    "test_real_record_generate_replay_targets_native_edit",
}


def verify_report(path: Path) -> None:
    """Require exactly the expected tests, each with a successful outcome."""
    cases = list(ElementTree.parse(path).iter("testcase"))
    names = [case.get("name") for case in cases]
    if len(cases) != len(REQUIRED_TESTS) or set(names) != REQUIRED_TESTS:
        raise RuntimeError(f"Recorder acceptance is incomplete: executed {names!r}")
    if any(case.find(tag) is not None for case in cases for tag in ("skipped", "failure", "error")):
        raise RuntimeError("Recorder acceptance requires PASS for every test; SKIP is unverified")


def main() -> int:
    """Execute the real suite and validate its fresh JUnit evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/recorder-acceptance.xml"))
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/component/test_recorder_windows.py",
            "-q",
            "-ra",
            "--dolphin-video=off",
            "--dolphin-trace=off",
            f"--junitxml={output}",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "DOLPHIN_RECORDER_ACCEPTANCE": "1"},
        check=False,
    )
    if result.returncode:
        return result.returncode
    try:
        verify_report(output)
    except (RuntimeError, OSError, ElementTree.ParseError) as exc:
        print(f"Recorder acceptance failed: {exc}", file=sys.stderr)
        return 1
    print(f"Recorder acceptance: all {len(REQUIRED_TESTS)} required tests PASS. Evidence: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
