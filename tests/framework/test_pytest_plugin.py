"""Tests for the pytest plugin's artifact handling."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dolphin_desktop import pytest_plugin as plugin

# Trace run directory naming


class TestTraceRunDirName:
    def test_repeated_calls_within_one_second_differ(self):
        nodeid = "tests/test_login.py::test_user_can_log_in"
        names = {plugin._trace_run_dir_name(nodeid) for _ in range(50)}
        assert len(names) == 50

    def test_nodeids_sharing_the_first_80_chars_differ(self):
        prefix = "tests/test_a_very_long_module_name_that_keeps_going.py::test_parametrised_case"
        a = plugin._trace_run_dir_name(f"{prefix}[alpha]")
        b = plugin._trace_run_dir_name(f"{prefix}[beta]")
        assert a != b

    def test_name_is_filesystem_safe(self):
        name = plugin._trace_run_dir_name("tests/test_x.py::test_y[a/b:c*d]")
        assert not set(name) & set('/\\:*?"<>|')

    def test_name_length_is_bounded(self):
        name = plugin._trace_run_dir_name("tests/test_x.py::test_y" + "z" * 500)
        assert len(name) <= 100

    def test_name_starts_with_the_sanitised_nodeid(self):
        assert plugin._trace_run_dir_name("tests/test_x.py::test_y").startswith(
            "tests_test_x.py__test_y_"
        )


# Screenshot / video file naming


class TestArtifactFileStem:
    def test_nodeids_sharing_the_first_80_chars_differ(self):
        """Two parametrisations must not overwrite each other's .png / .mp4."""
        prefix = "tests/test_a_very_long_module_name_that_keeps_going.py::test_parametrised_case"
        a = plugin._artifact_file_stem(f"{prefix}[alpha]")
        b = plugin._artifact_file_stem(f"{prefix}[beta]")
        assert a != b

    def test_repeated_calls_differ(self):
        nodeid = "tests/test_login.py::test_user_can_log_in"
        assert len({plugin._artifact_file_stem(nodeid) for _ in range(50)}) == 50

    def test_stem_is_filesystem_safe(self):
        stem = plugin._artifact_file_stem("tests/test_x.py::test_y[a/b:c*d]")
        assert not set(stem) & set('/\\:*?"<>|')

    def test_stem_length_is_bounded(self):
        assert len(plugin._artifact_file_stem("tests/test_x.py::test_y" + "z" * 500)) <= 80


# xdist worker detection


class TestXdistWorkerId:
    def test_none_outside_a_worker(self):
        assert plugin._xdist_worker_id(SimpleNamespace()) is None  # type: ignore[arg-type]

    def test_worker_id_read_from_workerinput(self):
        config = SimpleNamespace(workerinput={"workerid": "gw3"})
        assert plugin._xdist_worker_id(config) == "gw3"  # type: ignore[arg-type]

    def test_falls_back_when_workerid_missing(self):
        config = SimpleNamespace(workerinput={})
        assert plugin._xdist_worker_id(config) == "worker"  # type: ignore[arg-type]


# Failure screenshot capture must never escape the hookwrapper


class _FakeItem:
    def __init__(self, rootpath, nodeid: str) -> None:
        self.nodeid = nodeid
        self.config = SimpleNamespace(rootpath=rootpath)
        self.sections: list[tuple[str, str, str]] = []

    def add_report_section(self, when: str, key: str, content: str) -> None:
        self.sections.append((when, key, content))


class _FakeImage:
    def __init__(self, saved: list) -> None:
        self._saved = saved

    def save(self, path) -> None:
        self._saved.append(path)
        path.write_bytes(b"png")


class TestCaptureFailureScreenshot:
    def test_grab_failure_returns_none(self, tmp_path, monkeypatch):
        from PIL import ImageGrab

        def _boom(**_kwargs):
            raise OSError("screen grab failed")

        monkeypatch.setattr(ImageGrab, "grab", _boom)
        item = _FakeItem(tmp_path, "tests/test_x.py::test_y")
        assert plugin._capture_failure_screenshot(item) is None  # type: ignore[arg-type]

    def test_save_failure_returns_none(self, tmp_path, monkeypatch):
        from PIL import ImageGrab

        class _Unsavable:
            def save(self, path):
                raise OSError(22, "Invalid argument")

        monkeypatch.setattr(ImageGrab, "grab", lambda **_kwargs: _Unsavable())
        item = _FakeItem(tmp_path, "tests/test_x.py::test_y")
        assert plugin._capture_failure_screenshot(item) is None  # type: ignore[arg-type]

    def test_failure_is_logged(self, tmp_path, monkeypatch, caplog):
        from PIL import ImageGrab

        def _boom(**_kwargs):
            raise OSError("screen grab failed")

        monkeypatch.setattr(ImageGrab, "grab", _boom)
        item = _FakeItem(tmp_path, "tests/test_x.py::test_y")
        with caplog.at_level("WARNING", logger="dolphin_desktop.plugin"):
            plugin._capture_failure_screenshot(item)  # type: ignore[arg-type]
        assert "not captured" in caplog.text

    def test_long_nodeid_is_truncated(self, tmp_path, monkeypatch):
        from PIL import ImageGrab

        monkeypatch.setattr(ImageGrab, "grab", lambda **_kwargs: _FakeImage([]))
        nodeid = "tests/test_x.py::test_y[" + "p" * 400 + "]"
        item = _FakeItem(tmp_path, nodeid)
        path = plugin._capture_failure_screenshot(item)  # type: ignore[arg-type]
        assert path is not None
        assert len(path.stem) <= 80

    def test_two_parametrisations_do_not_share_one_file(self, tmp_path, monkeypatch):
        from PIL import ImageGrab

        monkeypatch.setattr(ImageGrab, "grab", lambda **_kwargs: _FakeImage([]))
        prefix = "tests/test_a_very_long_module_name_that_keeps_going.py::test_case"
        first = plugin._capture_failure_screenshot(_FakeItem(tmp_path, f"{prefix}[alpha]"))  # type: ignore[arg-type]
        second = plugin._capture_failure_screenshot(_FakeItem(tmp_path, f"{prefix}[beta]"))  # type: ignore[arg-type]
        assert first != second

    def test_section_uses_the_reporting_phase(self, tmp_path, monkeypatch):
        from PIL import ImageGrab

        monkeypatch.setattr(ImageGrab, "grab", lambda **_kwargs: _FakeImage([]))
        item = _FakeItem(tmp_path, "tests/test_x.py::test_y")
        plugin._capture_failure_screenshot(item, "setup")  # type: ignore[arg-type]
        assert item.sections[0][0] == "setup"

    def test_grab_spans_every_monitor(self, tmp_path, monkeypatch):
        """Without all_screens the capture is the primary monitor, not the failing one."""
        from PIL import ImageGrab

        seen: dict = {}

        def _grab(**kwargs):
            seen.update(kwargs)
            return _FakeImage([])

        monkeypatch.setattr(ImageGrab, "grab", _grab)
        plugin._capture_failure_screenshot(_FakeItem(tmp_path, "tests/t.py::test_x"))  # type: ignore[arg-type]
        assert seen["all_screens"] is True


# Video recorder cleanup ownership


class _ExplodingRecorder:
    def __init__(self) -> None:
        self.discarded = False

    def stop(self) -> None:
        raise RuntimeError("ffmpeg died")

    def discard(self) -> None:
        self.discarded = True


class _FakeVideoItem:
    def __init__(self, rootpath) -> None:
        self.nodeid = "tests/test_x.py::test_y"
        self.stash = pytest.Stash()
        self.config = SimpleNamespace(
            rootpath=rootpath, getoption=lambda *_a, **_k: "dolphin-videos"
        )

    def add_report_section(self, when: str, key: str, content: str) -> None:
        pass


class TestVideoStashOwnership:
    def test_stash_survives_a_failing_stop(self, tmp_path):
        """The stash entry is the fixture's only signal that cleanup is still owed."""
        item = _FakeVideoItem(tmp_path)
        recorder = _ExplodingRecorder()
        item.stash[plugin._VIDEO_RECORDER_KEY] = recorder
        with pytest.raises(RuntimeError):
            plugin._handle_video(item, SimpleNamespace(failed=True), "call")  # type: ignore[arg-type]
        assert item.stash.get(plugin._VIDEO_RECORDER_KEY, None) is recorder


# Why a recording is missing is the only thing the recorder can still tell the user


class _UnencodableRecorder:
    def stop(self) -> None:
        pass

    def encode(self, path) -> None:
        raise RuntimeError("Recording is unplayable: ffmpeg was killed")

    def discard(self) -> None:
        pass


class TestVideoFailuresAreReported:
    def _start_video(self, monkeypatch, caplog, exc: RuntimeError, ffmpeg: str | None):
        from dolphin_desktop import _config as cfg
        from dolphin_desktop import _video

        class _Recorder:
            def __init__(self, **kwargs) -> None:
                pass

            def start(self) -> None:
                raise exc

        monkeypatch.setattr(cfg, "get_video_mode", lambda: "keepall")
        monkeypatch.setattr(_video, "VideoRecorder", _Recorder)
        monkeypatch.setattr(_video, "find_ffmpeg", lambda: ffmpeg)
        request = SimpleNamespace(node=SimpleNamespace(stash=pytest.Stash()))
        with caplog.at_level("DEBUG", logger="dolphin_desktop.plugin"):
            fixture = plugin._dolphin_video.__wrapped__(request)
            next(fixture)
            fixture.close()
        return caplog.records

    def test_a_capture_refusal_is_warned_about(self, monkeypatch, caplog):
        """gdigrab refusals (session 0, locked workstation) must not vanish."""
        records = self._start_video(
            monkeypatch, caplog, RuntimeError("ffmpeg exited immediately (code 1)"), "ffmpeg.exe"
        )
        assert [r.levelname for r in records] == ["WARNING"]
        assert "ffmpeg exited immediately" in records[0].getMessage()

    def test_a_missing_ffmpeg_stays_quiet(self, monkeypatch, caplog):
        records = self._start_video(
            monkeypatch, caplog, RuntimeError("Video recording requires ffmpeg."), None
        )
        assert [r.levelname for r in records] == ["DEBUG"]

    def test_a_failed_encode_is_logged_as_well_as_sectioned(self, tmp_path, caplog):
        item = _FakeVideoItem(tmp_path)
        item.stash[plugin._VIDEO_RECORDER_KEY] = _UnencodableRecorder()
        with caplog.at_level("WARNING", logger="dolphin_desktop.plugin"):
            assert plugin._handle_video(item, SimpleNamespace(failed=True), "call") is None
        assert "unplayable" in caplog.text


# HTML fallback report escaping


class TestHtmlReportEscaping:
    def _render(self, tmp_path, row: dict) -> str:
        plugin._session_reports.clear()
        plugin._session_reports.append(row)
        try:
            out = tmp_path / "report.html"
            plugin._generate_html_report(out)
            return out.read_text(encoding="utf-8")
        finally:
            plugin._session_reports.clear()

    def _row(self, **overrides) -> dict:
        row = {
            "nodeid": "tests/test_x.py::test_y",
            "outcome": "failed",
            "duration": 0.1,
            "screenshot": None,
            "trace": None,
            "video": None,
        }
        row.update(overrides)
        return row

    def test_nodeid_markup_is_escaped(self, tmp_path):
        html = self._render(
            tmp_path, self._row(nodeid="tests/test_x.py::test_y[<img src=x onerror=boom>]")
        )
        assert "<img src=x" not in html
        assert "&lt;img src=x" in html

    def test_artifact_path_quotes_cannot_break_out_of_the_attribute(self, tmp_path):
        html = self._render(tmp_path, self._row(trace=r'traces/a" onclick="boom'))
        assert 'onclick="boom' not in html
        assert "&quot;" in html


# Hookwrapper isolation


class TestMakereportIsolation:
    def test_collection_error_does_not_escape(self, monkeypatch, caplog):
        def _boom(item, call, report):
            raise RuntimeError("artifact backend exploded")

        monkeypatch.setattr(plugin, "_collect_artifacts", _boom)
        item = SimpleNamespace(nodeid="tests/test_x.py::test_y")
        hook = plugin.pytest_runtest_makereport(item, SimpleNamespace(when="call"))
        next(hook)
        with caplog.at_level("WARNING", logger="dolphin_desktop.plugin"):
            with pytest.raises(StopIteration):
                hook.send(SimpleNamespace(get_result=lambda: SimpleNamespace(failed=True)))
        assert "artifact collection failed" in caplog.text
