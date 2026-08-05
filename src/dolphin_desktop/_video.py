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
from typing import IO

VALID_MODES = ("off", "keepfailedonly", "keepall")

_FFMPEG_ENV = "DOLPHIN_FFMPEG"

# Windows: keep the ffmpeg child process from opening a console window.
_CREATE_NO_WINDOW = 0x08000000

# Seconds start() waits before deciding ffmpeg came up. gdigrab refusals
# (session 0, a locked workstation, a dropped RDP session) exit at once.
_SPAWN_PROBE = 0.5

# Bytes of ffmpeg's stderr quoted back in an error message.
_STDERR_TAIL = 2000


def find_ffmpeg() -> str | None:
    """Return path to ffmpeg binary, or ``None`` if not found."""
    env = os.environ.get(_FFMPEG_ENV)
    if env and Path(env).is_file():
        return env
    return shutil.which("ffmpeg")


def _has_moov(path: Path) -> bool:
    """True when the MP4 at *path* carries a ``moov`` atom.

    ffmpeg writes the index only when it exits cleanly, so a killed capture
    leaves a non-empty file that no player can open — size alone cannot tell
    the two apart.
    """
    try:
        with path.open("rb") as fh:
            while True:
                header = fh.read(8)
                if len(header) < 8:
                    return False
                size = int.from_bytes(header[0:4], "big")
                if header[4:8] == b"moov":
                    return True
                if size == 1:  # 64-bit extended size follows the box type
                    extended = fh.read(8)
                    if len(extended) < 8:
                        return False
                    size = int.from_bytes(extended, "big")
                    if size < 16:
                        return False
                    fh.seek(size - 16, os.SEEK_CUR)
                elif size < 8:  # 0 means "to end of file" — no further boxes
                    return False
                else:
                    fh.seek(size - 8, os.SEEK_CUR)
    except OSError:
        return False


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
        self._errfile: Path | None = None
        self._errhandle: IO[bytes] | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._started = False

    # Lifecycle

    def start(self) -> None:
        """Start an ffmpeg gdigrab process recording the desktop to MP4.

        Raises ``RuntimeError`` if ffmpeg cannot be found, or if it exits
        straight away — a gdigrab refusal (session 0, a locked workstation, a
        dropped RDP session) would otherwise only ever surface as "No frames
        captured" with the real cause gone.
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
        self._errfile = self._tmpdir / "ffmpeg.log"

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
        # A file rather than a pipe: nothing drains a pipe while the test runs,
        # so a chatty ffmpeg would block once the pipe buffer filled.
        self._errhandle = self._errfile.open("wb")
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=self._errhandle,
            creationflags=creationflags,
        )
        self._started = True

        try:
            self._proc.wait(timeout=_SPAWN_PROBE)
        except subprocess.TimeoutExpired:
            return
        code = self._proc.returncode
        reason = self._stderr_text() or "<ffmpeg wrote nothing to stderr>"
        self.discard()
        raise RuntimeError(
            f"ffmpeg exited immediately (code {code}) instead of capturing the screen: {reason}"
        )

    def _stderr_text(self) -> str:
        """Return the tail of what ffmpeg wrote to stderr, or ``""``."""
        try:
            if self._errhandle is not None:
                self._errhandle.flush()
            if self._errfile is None or not self._errfile.exists():
                return ""
            return self._errfile.read_bytes()[-_STDERR_TAIL:].decode("utf-8", "replace").strip()
        except OSError:
            return ""

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
                # Without this the child is never reaped and, on Windows, its
                # handle on capture.mp4 can outlive discard()'s rmtree.
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass

        self._close_stderr()

    def _close_stderr(self) -> None:
        if self._errhandle is None:
            return
        try:
            self._errhandle.close()
        except Exception:
            pass
        self._errhandle = None

    # Output

    def encode(self, output_path: Path) -> Path:
        """Save the recorded MP4 to *output_path*.

        ffmpeg already produced an encoded MP4 during capture, so this simply
        copies it to the destination.  Returns *output_path* on success.
        Raises ``RuntimeError`` if nothing was recorded or if the capture was
        cut short before ffmpeg could finalise the container.
        """
        if self._outfile is None or not self._outfile.exists() or self._outfile.stat().st_size == 0:
            detail = self._stderr_text()
            raise RuntimeError(
                "No frames captured — nothing to encode."
                + (f" ffmpeg said: {detail}" if detail else "")
            )
        if not _has_moov(self._outfile):
            raise RuntimeError(
                "Recording is unplayable: ffmpeg was killed before it wrote the "
                "MP4 index (no moov atom), so the capture is discarded rather "
                "than reported as an artifact."
            )

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._outfile, output_path)
        return output_path

    def discard(self) -> None:
        """Stop the capture and delete the temporary recording directory."""
        self.stop()
        self._close_stderr()
        if self._tmpdir is not None and self._tmpdir.exists():
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            if self._tmpdir.exists():
                # Keep the paths so a later call can retry — clearing them here
                # would strand the directory and the MP4 inside it.
                return
        self._tmpdir = None
        self._outfile = None
        self._errfile = None
