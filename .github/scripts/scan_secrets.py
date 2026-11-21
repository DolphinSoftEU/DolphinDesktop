"""Run the locked secret hook on tracked files, preserving spaces in paths."""

import subprocess
import sys

files = subprocess.check_output(["git", "ls-files", "-z"]).decode("utf-8").split("\0")
failed = False
for start in range(0, len(files), 50):
    batch = [path for path in files[start : start + 50] if path]
    if batch:
        result = subprocess.run(
            ["detect-secrets-hook", "--baseline", ".secrets.baseline", *batch],
            check=False,
        )
        failed |= result.returncode != 0
sys.exit(1 if failed else 0)
