"""Tests for ffmpeg discovery and recorder edge cases."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from dolphin_desktop import _video
from dolphin_desktop._video import VideoRecorder, _has_moov


def _box(kind: bytes, size: int, *, extended: bool = False) -> bytes:
    if extended:
        return (1).to_bytes(4, "big") + kind + size.to_bytes(8, "big") + b"\0" * (size - 16)
    return size.to_bytes(4, "big") + kind + b"\0" * (size - 8)


def test_find_ffmpeg_prefers_a_valid_environment_path(tmp_path: Path, monkeypatch) -> None:
    configured = tmp_path / "ffmpeg.exe"
    configured.write_bytes(b"binary")
    monkeypatch.setenv("DOLPHIN_FFMPEG", str(configured))
    monkeypatch.setattr(_video.shutil, "which", lambda _: pytest.fail("PATH lookup was used"))

    assert _video.find_ffmpeg() == str(configured)


def test_find_ffmpeg_falls_back_to_path_when_environment_path_is_invalid(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DOLPHIN_FFMPEG", str(tmp_path / "missing-ffmpeg.exe"))
    monkeypatch.setattr(_video.shutil, "which", lambda name: f"C:/tools/{name}.exe")

    assert _video.find_ffmpeg() == "C:/tools/ffmpeg.exe"


def test_has_moov_walks_past_a_valid_extended_size_box(tmp_path: Path) -> None:
    path = tmp_path / "extended.mp4"
    path.write_bytes(_box(b"mdat", 24, extended=True) + _box(b"moov", 8))

    assert _has_moov(path) is True


@pytest.mark.parametrize(
    "body",
    [
        (1).to_bytes(4, "big") + b"mdat" + b"\0" * 7,
        (1).to_bytes(4, "big") + b"mdat" + (15).to_bytes(8, "big"),
    ],
)
def test_has_moov_rejects_truncated_or_invalid_extended_size(
    tmp_path: Path,
    body: bytes,
) -> None:
    path = tmp_path / "broken.mp4"
    path.write_bytes(body)

    assert _has_moov(path) is False


def test_start_fails_before_creating_capture_state_when_ffmpeg_is_missing() -> None:
    recorder = VideoRecorder()

    with patch.object(_video, "find_ffmpeg", return_value=None):
        with pytest.raises(RuntimeError, match="requires ffmpeg"):
            recorder.start()

    assert recorder._started is False
    assert recorder._tmpdir is None


def test_start_is_idempotent_when_capture_is_already_running() -> None:
    recorder = VideoRecorder()
    recorder._started = True

    with (
        patch.object(_video, "find_ffmpeg") as find,
        patch.object(_video.subprocess, "Popen") as popen,
    ):
        recorder.start()

    find.assert_not_called()
    popen.assert_not_called()


def test_start_builds_the_gdigrab_command_and_keeps_a_live_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = VideoRecorder(fps=12)
    process = MagicMock()
    process.wait.side_effect = subprocess.TimeoutExpired("ffmpeg", 0.5)
    monkeypatch.setattr(_video, "find_ffmpeg", lambda: "ffmpeg.exe")
    monkeypatch.setattr(_video.subprocess, "Popen", MagicMock(return_value=process))

    try:
        recorder.start()
        assert recorder._started is True
        (command,) = _video.subprocess.Popen.call_args.args
        assert command[:8] == [
            "ffmpeg.exe",
            "-y",
            "-f",
            "gdigrab",
            "-framerate",
            "12",
            "-i",
            "desktop",
        ]
        assert _video.subprocess.Popen.call_args.kwargs["stdin"] is subprocess.PIPE
        assert _video.subprocess.Popen.call_args.kwargs["stdout"] is subprocess.DEVNULL
        assert _video.subprocess.Popen.call_args.kwargs["creationflags"] == (
            _video._CREATE_NO_WINDOW if os.name == "nt" else 0
        )
    finally:
        recorder.discard()


def test_stop_handles_a_process_without_stdin_and_closes_stderr() -> None:
    recorder = VideoRecorder()
    recorder._started = True
    process = MagicMock(stdin=None)
    recorder._proc = process
    error_file = MagicMock()
    recorder._errhandle = error_file

    recorder.stop()

    process.wait.assert_called_once_with(timeout=5)
    process.terminate.assert_not_called()
    process.kill.assert_not_called()
    error_file.close.assert_called_once_with()
    assert recorder._started is False
    assert recorder._errhandle is None


def test_stop_terminates_when_graceful_wait_times_out_but_does_not_kill() -> None:
    recorder = VideoRecorder()
    recorder._started = True
    process = MagicMock(stdin=None)
    process.wait.side_effect = [subprocess.TimeoutExpired("ffmpeg", 5), None]
    recorder._proc = process

    recorder.stop()

    process.wait.assert_has_calls([call(timeout=5), call(timeout=2)])
    process.terminate.assert_called_once_with()
    process.kill.assert_not_called()


def test_close_stderr_clears_a_handle_even_when_close_raises() -> None:
    recorder = VideoRecorder()
    error_file = MagicMock()
    error_file.close.side_effect = OSError("already closed")
    recorder._errhandle = error_file

    recorder._close_stderr()

    error_file.close.assert_called_once_with()
    assert recorder._errhandle is None


def test_video_recorder_reports_missing_capture_and_rejects_frame_rate() -> None:
    from dolphin_desktop._video import VideoRecorder

    with pytest.raises(ValueError, match="fps"):
        VideoRecorder(fps=100)
    with pytest.raises(RuntimeError, match="No frames captured"):
        VideoRecorder().encode(Path("missing.mp4"))


@pytest.mark.parametrize("fps", [0, 31])
def test_video_recorder_rejects_fps_outside_configured_range(fps: int) -> None:
    """Direct recorder construction must match config's 1..30 FPS contract."""
    with pytest.raises(ValueError, match="fps"):
        VideoRecorder(fps=fps)


def test_video_moov_probe_rejects_truncated_and_accepts_valid_file(tmp_path: Path) -> None:
    from dolphin_desktop._video import _has_moov

    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"\0")
    valid = tmp_path / "valid.mp4"
    valid.write_bytes((8).to_bytes(4, "big") + b"moov")
    assert _has_moov(broken) is False
    assert _has_moov(valid) is True
