"""Tests for VideoRecorder process lifecycle and artifact validation."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dolphin_desktop._video import VideoRecorder, _has_moov


def _mp4(*boxes: tuple[bytes, int]) -> bytes:
    """Build a minimal MP4 byte stream out of (type, payload_size) boxes."""
    out = b""
    for kind, payload in boxes:
        out += (8 + payload).to_bytes(4, "big") + kind + b"\x00" * payload
    return out


class TestHasMoov:
    """A killed ffmpeg leaves a sizeable file that no player can open."""

    def test_finalised_file_is_accepted(self, tmp_path: Path):
        path = tmp_path / "ok.mp4"
        path.write_bytes(_mp4((b"ftyp", 16), (b"mdat", 512), (b"moov", 64)))
        assert _has_moov(path) is True

    def test_file_without_the_index_is_rejected(self, tmp_path: Path):
        path = tmp_path / "killed.mp4"
        path.write_bytes(_mp4((b"ftyp", 16), (b"mdat", 512)))
        assert _has_moov(path) is False

    def test_truncated_box_header_is_rejected(self, tmp_path: Path):
        path = tmp_path / "stub.mp4"
        path.write_bytes(b"\x00\x00")
        assert _has_moov(path) is False

    def test_zero_sized_box_terminates_the_walk(self, tmp_path: Path):
        path = tmp_path / "open_ended.mp4"
        path.write_bytes(b"\x00\x00\x00\x00" + b"mdat" + b"\x00" * 64)
        assert _has_moov(path) is False

    def test_missing_file_is_rejected(self, tmp_path: Path):
        assert _has_moov(tmp_path / "absent.mp4") is False


class TestEncodeRejectsUnplayableCaptures:
    """An MP4 without a moov atom must not be reported as a saved artifact."""

    def _recorder(self, tmp_path: Path, body: bytes) -> VideoRecorder:
        rec = VideoRecorder()
        rec._tmpdir = tmp_path
        rec._outfile = tmp_path / "capture.mp4"
        rec._outfile.write_bytes(body)
        return rec

    def test_finalised_capture_is_copied(self, tmp_path: Path):
        rec = self._recorder(tmp_path, _mp4((b"ftyp", 8), (b"moov", 32)))
        out = tmp_path / "out" / "test.mp4"
        assert rec.encode(out) == out
        assert out.exists()

    def test_moovless_capture_raises_and_copies_nothing(self, tmp_path: Path):
        rec = self._recorder(tmp_path, _mp4((b"ftyp", 8), (b"mdat", 4096)))
        out = tmp_path / "out" / "test.mp4"
        with pytest.raises(RuntimeError, match="moov"):
            rec.encode(out)
        assert not out.exists()

    def test_empty_capture_still_reports_no_frames(self, tmp_path: Path):
        rec = self._recorder(tmp_path, b"")
        with pytest.raises(RuntimeError, match="No frames captured"):
            rec.encode(tmp_path / "out.mp4")

    def test_no_frames_message_quotes_ffmpeg(self, tmp_path: Path):
        rec = self._recorder(tmp_path, b"")
        rec._errfile = tmp_path / "ffmpeg.log"
        rec._errfile.write_bytes(b"Could not find video device with name [desktop]")
        with pytest.raises(RuntimeError, match="Could not find video device"):
            rec.encode(tmp_path / "out.mp4")


class _FakeProc:
    """Popen stand-in whose wait() can be told to hang."""

    def __init__(self, *, hangs: bool = True, returncode: int = 0) -> None:
        self.stdin = MagicMock()
        self.returncode = returncode
        self.waits: list[float | None] = []
        self.terminated = False
        self.killed = False
        self._hangs = hangs

    def wait(self, timeout: float | None = None) -> int:
        self.waits.append(timeout)
        if self._hangs:
            raise subprocess.TimeoutExpired("ffmpeg", timeout or 0)
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self._hangs = False


class TestStopReapsAKilledChild:
    def test_kill_is_followed_by_a_wait(self):
        rec = VideoRecorder()
        rec._started = True
        proc = _FakeProc(hangs=True)
        rec._proc = proc  # type: ignore[assignment]

        rec.stop()

        assert proc.killed is True
        # q-wait, terminate-wait, then the reaping wait after kill().
        assert len(proc.waits) == 3

    def test_clean_exit_does_not_terminate(self):
        rec = VideoRecorder()
        rec._started = True
        proc = _FakeProc(hangs=False)
        rec._proc = proc  # type: ignore[assignment]

        rec.stop()

        assert proc.terminated is False
        assert proc.killed is False


class TestDiscardStopsTheProcess:
    """start() → discard() must not leave ffmpeg running on the desktop."""

    def test_discard_stops_a_running_capture(self, tmp_path: Path):
        rec = VideoRecorder()
        rec._started = True
        rec._tmpdir = tmp_path / "recording"
        rec._tmpdir.mkdir()
        (rec._tmpdir / "capture.mp4").write_bytes(b"x")
        rec._outfile = rec._tmpdir / "capture.mp4"
        proc = _FakeProc(hangs=False)
        rec._proc = proc  # type: ignore[assignment]

        rec.discard()

        assert rec._started is False
        assert proc.stdin.write.call_args.args == (b"q",)
        assert not (tmp_path / "recording").exists()
        assert rec._tmpdir is None

    def test_an_undeletable_directory_stays_reachable(self, tmp_path: Path):
        """Clearing _tmpdir on a failed rmtree strands the directory forever."""
        rec = VideoRecorder()
        tmpdir = tmp_path / "locked"
        tmpdir.mkdir()
        rec._tmpdir = tmpdir

        with patch("dolphin_desktop._video.shutil.rmtree"):
            rec.discard()

        assert rec._tmpdir == tmpdir

    def test_a_second_discard_is_a_no_op(self, tmp_path: Path):
        rec = VideoRecorder()
        rec._tmpdir = tmp_path / "gone"
        rec.discard()
        rec.discard()
        assert rec._tmpdir is None


class TestStartSurfacesSpawnFailures:
    """gdigrab refusals used to be discarded with stderr=DEVNULL."""

    def _spawn(self, tmp_path: Path, rec: VideoRecorder, stderr: bytes, hangs: bool):
        def _popen(cmd, **kwargs):
            handle = kwargs["stderr"]
            handle.write(stderr)
            handle.flush()
            return _FakeProc(hangs=hangs, returncode=1)

        return patch("dolphin_desktop._video.subprocess.Popen", _popen)

    def test_immediate_exit_raises_with_the_ffmpeg_output(self, tmp_path: Path):
        rec = VideoRecorder()
        message = b"gdigrab: Could not open desktop: session 0 has no display"
        with (
            patch("dolphin_desktop._video.find_ffmpeg", return_value="ffmpeg"),
            self._spawn(tmp_path, rec, message, hangs=False),
        ):
            with pytest.raises(RuntimeError, match="session 0 has no display"):
                rec.start()

        assert rec._tmpdir is None

    def test_a_live_process_is_left_alone(self, tmp_path: Path):
        rec = VideoRecorder()
        with (
            patch("dolphin_desktop._video.find_ffmpeg", return_value="ffmpeg"),
            self._spawn(tmp_path, rec, b"", hangs=True),
        ):
            rec.start()

        assert rec._started is True
        assert rec._tmpdir is not None
        rec._proc = None
        rec.discard()

    def test_stderr_is_not_a_pipe(self, tmp_path: Path):
        """Nothing drains a pipe during the test, so a chatty ffmpeg would block."""
        seen: dict[str, object] = {}

        def _popen(cmd, **kwargs):
            seen.update(kwargs)
            return _FakeProc(hangs=True)

        rec = VideoRecorder()
        with (
            patch("dolphin_desktop._video.find_ffmpeg", return_value="ffmpeg"),
            patch("dolphin_desktop._video.subprocess.Popen", _popen),
        ):
            rec.start()

        assert seen["stderr"] is not subprocess.PIPE
        assert seen["stderr"] is not subprocess.DEVNULL
        rec._proc = None
        rec.discard()
