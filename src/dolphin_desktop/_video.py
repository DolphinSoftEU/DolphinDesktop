"""Video recording for test runs.

Records the screen to MP4 (H.264) using an external ``ffmpeg -f gdigrab``
process.  Capture runs **out-of-process** on purpose: grabbing the screen in a
background thread inside the test process deadlocks with pywinauto's UIA/COM
calls on the main thread (both block, CPU drops to 0, and pytest-timeout's
thread method cannot interrupt the native call).  A separate ffmpeg process has
its own GDI/COM context, so it cannot contend with the test's UIA calls.

Modes
-----
off            — no recording
keepfailedonly — record all tests, keep MP4 only for failed ones (default)
keepall        — keep MP4 for every test

Environment variables
---------------------
DOLPHIN_VIDEO       — default mode (off / keepfailedonly / keepall)
DOLPHIN_VIDEO_FPS   — capture rate, integer, default 10
DOLPHIN_FFMPEG      — path to ffmpeg binary (falls back to PATH lookup)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

VALID_MODES = ("off", "keepfailedonly", "keepall")

_FFMPEG_ENV = "DOLPHIN_FFMPEG"

# Windows: keep the ffmpeg child process from opening a console window.
_CREATE_NO_WINDOW = 0x08000000


def find_ffmpeg() -> str | None:
    """Return path to ffmpeg binary, or ``None`` if not found."""
    env = os.environ.get(_FFMPEG_ENV)
    if env and Path(env).is_file():
        return env
    return shutil.which("ffmpeg")


class VideoRecorder:
    """Records the screen to MP4 via a background ffmpeg process.

    Typical lifecycle::

        rec = VideoRecorder(fps=10)
        rec.start()
        # ... test runs ...
        rec.stop()
        if failed:
            rec.encode(Path("output.mp4"))
        rec.discard()
    """

    def __init__(self, fps: int = 10) -> None:
        self._fps = max(1, min(fps, 30))
        self._tmpdir: Path | None = None
        self._outfile: Path | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start an ffmpeg gdigrab process recording the desktop to MP4.

        Raises ``RuntimeError`` if ffmpeg cannot be found.
        Silently skips if already started.
        """
        if self._started:
            return

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise RuntimeError(
                "Video recording requires ffmpeg. Install ffmpeg or set the "
                "DOLPHIN_FFMPEG environment variable to the binary path."
            )

        self._tmpdir = Path(tempfile.mkdtemp(prefix="dolphin_video_"))
        self._outfile = self._tmpdir / "capture.mp4"

        cmd = [
            ffmpeg,
            "-y",
            "-f",
            "gdigrab",
            "-framerate",
            str(self._fps),
            "-i",
            "desktop",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",  # broad player compatibility
            "-loglevel",
            "error",
            str(self._outfile),
        ]

        creationflags = _CREATE_NO_WINDOW if os.name == "nt" else 0
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self._started = True

    def stop(self) -> None:
        """Stop the ffmpeg process, letting it finalise the MP4 container.

        Sends ``q`` on ffmpeg's stdin so the moov atom is written and the file
        stays playable; falls back to terminate/kill if it does not exit.
        """
        if not self._started:
            return
        self._started = False

        proc = self._proc
        if proc is None:
            return

        try:
            if proc.stdin is not None:
                proc.stdin.write(b"q")
                proc.stdin.flush()
                proc.stdin.close()
        except Exception:
            pass

        try:
            proc.wait(timeout=5)
        except Exception:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except Exception:
                proc.kill()

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def encode(self, output_path: Path) -> Path:
        """Save the recorded MP4 to *output_path*.

        ffmpeg already produced an encoded MP4 during capture, so this simply
        copies it to the destination.  Returns *output_path* on success.
        Raises ``RuntimeError`` if nothing was recorded.
        """
        if self._outfile is None or not self._outfile.exists() or self._outfile.stat().st_size == 0:
            raise RuntimeError("No frames captured — nothing to encode.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._outfile, output_path)
        return output_path

    def discard(self) -> None:
        """Delete the temporary recording directory."""
        if self._tmpdir and self._tmpdir.exists():
            shutil.rmtree(self._tmpdir, ignore_errors=True)
        self._tmpdir = None
        self._outfile = None
