# KAN-576 — recorder fixes and verification

Date: 2026-11-04. Addresses the issue description and retest comments from
2026-10-20 and 2026-10-21.

Changes:

- Normalize the repeated pywin32 thread/PID result before comparing it with
  the original snapshot. Both list and tuple results classify stable native
  Edit fields correctly; the focus, foreground, style and PID checks remain.
- Declare the native ctypes signatures for hook installation, forwarding,
  removal, message handling and focus queries. Callback LRESULT uses pointer
  width on x86/x64. Use a private user32 loader with last-error support.
- Wait for hook installation in `start()` and report installation failure to
  the caller. A partially installed hook is removed and the worker is not
  started. Hook cleanup also runs after a message-loop error.
- Add regressions for list results, pointer-width callback arguments/results,
  failed installations and message-loop errors.
- Add a Windows component test for installation and cleanup of production
  hooks without generating input. The interactive focus-switching test and
  its window/input helpers were removed at the user's request.

Verification commands:

```powershell
.venv\Scripts\python.exe -m pytest -q -rs tests/framework/test_recorder.py tests/component/test_recorder_windows.py --dolphin-video=off --dolphin-trace=off --junitxml=artifacts/KAN-576-fix/recorder.xml
.venv\Scripts\python.exe -m pytest -q -n 4 -m unit --dolphin-video=off --dolphin-trace=off --junitxml=artifacts/KAN-576-fix/unit-parallel.xml
.venv\Scripts\ruff.exe check .
.venv\Scripts\ruff.exe format --check .
.venv\Scripts\python.exe -m mypy src/
```

Result after removing the interactive test: **173 passed, 0 skipped**.
Ruff and formatting passed. Mypy passed for the unchanged production code.
The real Windows hook installation/removal test passed without generating input.
The full unit suite passed: **4448 passed, 17 skipped** in 183.70 seconds.
The earlier serial unit run was interrupted and replaced by this complete
four-worker run: suite-level configuration changes re-enabled FFmpeg during
the serial run despite its initial CLI setting. The incomplete serial log
is not a passing result. Full unit evidence is in
`artifacts/KAN-576-fix/unit-parallel.log` and `unit-parallel.xml`.

The removed interactive test previously skipped because Windows refused
`SetForegroundWindow` for the owned AUT, before sending input or installing
hooks. Live password transitions and the positive plaintext control remain
unverified on this desktop; removing the test does not establish an
interactive PASS.
