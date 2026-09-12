"""Tests for the pytest plugin's artifact handling."""


# Trace run directory naming

from __future__ import annotations

import builtins
import subprocess
import sys
import types
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from dolphin_desktop import pytest_plugin as plugin
from dolphin_desktop._exceptions import ElementNotFoundError

REPO_ROOT = Path(__file__).resolve().parents[2]


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
    def test_makereport_redacts_longrepr_before_reporters_serialize_it(
        self, monkeypatch
    ):
        monkeypatch.setattr(plugin, "_collect_artifacts", lambda *_args: None)
        report = SimpleNamespace(
            failed=True,
            longrepr='login="DESKTOP_LOGIN_2c4b"',
        )
        hook = plugin.pytest_runtest_makereport(
            SimpleNamespace(nodeid="tests/test_x.py::test_y"),
            SimpleNamespace(when="call"),
        )
        next(hook)
        with pytest.raises(StopIteration):
            hook.send(SimpleNamespace(get_result=lambda: report))

        assert report.longrepr == 'login="***"'

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


class _Options:
    def __init__(self, rootpath: Path, **options) -> None:
        self.rootpath = rootpath
        self.options = options

    def getoption(self, name, default=None):
        return self.options.get(name, default)


class _Item:
    def __init__(self, tmp_path: Path, nodeid: str = "tests/test_x.py::test_y", **options) -> None:
        self.nodeid = nodeid
        self.stash = pytest.Stash()
        self.config = _Options(tmp_path, **options)
        self.location = ("test_x.py", 1, "test_y")
        self.sections: list[tuple[str, str, str]] = []

    def add_report_section(self, when: str, key: str, content: str) -> None:
        self.sections.append((when, key, content))


def _report(*, failed: bool, outcome: str | None = None, **kwargs):
    return SimpleNamespace(
        failed=failed,
        outcome=outcome or ("failed" if failed else "passed"),
        longrepr=kwargs.pop("longrepr", None),
        user_properties=[],
        duration=kwargs.pop("duration", 0.25),
        capstdout=kwargs.pop("capstdout", ""),
        capstderr=kwargs.pop("capstderr", ""),
        **kwargs,
    )


def _request(tmp_path: Path, nodeid: str = "tests/test_x.py::test_y", **options):
    node = SimpleNamespace(
        nodeid=nodeid,
        stash=pytest.Stash(),
        get_closest_marker=lambda _name: None,
    )
    return SimpleNamespace(node=node, config=_Options(tmp_path, **options))


def _fake_allure(calls: list[tuple], file_calls: list[tuple] | None = None):
    class _AttachmentType:
        PNG = "png"
        HTML = "html"
        ZIP = "zip"
        TEXT = "text"
        MP4 = "mp4"

    class _Attach:
        def __call__(self, *args, **kwargs):
            calls.append((args, kwargs))

        def file(self, *args, **kwargs):
            assert file_calls is not None
            file_calls.append((args, kwargs))

    module = types.ModuleType("allure")
    module.attachment_type = _AttachmentType
    module.attach = _Attach()
    return module


class TestBasicHooksAndOptions:
    def test_sessionstart_clears_previous_report_rows(self):
        plugin._session_reports[:] = [{"nodeid": "stale"}]
        plugin.pytest_sessionstart(SimpleNamespace())
        assert plugin._session_reports == []

    def test_runtest_teardown_noop_without_live_processes(self, monkeypatch):
        from dolphin_desktop import _application

        monkeypatch.setattr(_application, "_live_pids", set())
        plugin.pytest_runtest_teardown(SimpleNamespace(nodeid="test"), None)

    def test_runtest_teardown_kills_processes_and_always_discards_them(self, monkeypatch):
        from dolphin_desktop import _application

        api_calls: list[tuple] = []
        api = types.SimpleNamespace(
            OpenProcess=lambda *args: api_calls.append(("open", *args)) or "handle",
            TerminateProcess=lambda *args: api_calls.append(("terminate", *args)),
            CloseHandle=lambda *args: api_calls.append(("close", *args)),
        )
        con = types.SimpleNamespace(PROCESS_TERMINATE=7)
        monkeypatch.setitem(sys.modules, "win32api", api)
        monkeypatch.setitem(sys.modules, "win32con", con)
        live = {101, 102}
        monkeypatch.setattr(_application, "_live_pids", live)

        plugin.pytest_runtest_teardown(SimpleNamespace(nodeid="test"), None)

        assert live == set()
        assert ("terminate", "handle", 1) in api_calls

    def test_runtest_teardown_discards_pid_when_win32_cleanup_fails(self, monkeypatch):
        from dolphin_desktop import _application

        monkeypatch.setitem(
            sys.modules,
            "win32api",
            types.SimpleNamespace(
                OpenProcess=lambda *_args: (_ for _ in ()).throw(OSError("gone"))
            ),
        )
        monkeypatch.setitem(sys.modules, "win32con", types.SimpleNamespace(PROCESS_TERMINATE=7))
        live = {404}
        monkeypatch.setattr(_application, "_live_pids", live)

        plugin.pytest_runtest_teardown(SimpleNamespace(nodeid="test"), None)

        assert live == set()

    def test_runtest_teardown_uses_anchored_handle_and_keeps_it_on_failure(
        self, monkeypatch
    ):
        from dolphin_desktop import _application

        calls: list[tuple] = []
        api = types.SimpleNamespace(
            OpenProcess=lambda *_args: pytest.fail("PID must not be reopened"),
            TerminateProcess=lambda *args: calls.append(("terminate", *args))
            or (_ for _ in ()).throw(OSError("access denied")),
            CloseHandle=lambda *args: calls.append(("close", *args)),
        )
        monkeypatch.setitem(sys.modules, "win32api", api)
        live = {505}
        handles = {505: "original-process-handle"}
        monkeypatch.setattr(_application, "_live_pids", live)
        monkeypatch.setattr(_application, "_owned_process_handles", handles)

        plugin.pytest_runtest_teardown(SimpleNamespace(nodeid="reuse"), None)

        assert calls == [("terminate", "original-process-handle", 1)]
        assert live == {505}
        assert handles == {505: "original-process-handle"}

    def test_runtest_teardown_terminates_original_process_handle_after_pid_reuse(
        self, monkeypatch
    ):
        from dolphin_desktop import _application

        calls: list[tuple] = []
        api = types.SimpleNamespace(
            OpenProcess=lambda *_args: pytest.fail("PID must not be reopened"),
            TerminateProcess=lambda *args: calls.append(("terminate", *args)),
            CloseHandle=lambda *args: calls.append(("close", *args)),
        )
        monkeypatch.setitem(sys.modules, "win32api", api)
        live = {506}
        handles = {506: "original-process-handle"}
        monkeypatch.setattr(_application, "_live_pids", live)
        monkeypatch.setattr(_application, "_owned_process_handles", handles)

        plugin.pytest_runtest_teardown(SimpleNamespace(nodeid="reuse"), None)

        assert calls == [
            ("terminate", "original-process-handle", 1),
            ("close", "original-process-handle"),
        ]
        assert live == set()
        assert handles == {}

    def test_runtest_teardown_fails_closed_when_anchor_could_not_be_opened(self, monkeypatch):
        from dolphin_desktop import _application

        monkeypatch.setitem(
            sys.modules,
            "win32api",
            types.SimpleNamespace(
                OpenProcess=lambda *_args: pytest.fail("must not fall back to PID cleanup"),
                TerminateProcess=lambda *_args: pytest.fail("must not terminate by PID"),
            ),
        )
        live = {508}
        unanchored = {508}
        monkeypatch.setattr(_application, "_live_pids", live)
        monkeypatch.setattr(_application, "_unanchored_pids", unanchored)

        plugin.pytest_runtest_teardown(SimpleNamespace(nodeid="unanchored"), None)

        assert live == {508}
        assert unanchored == {508}

    @pytest.mark.parametrize(
        ("report", "expected"),
        [
            (None, False),
            (SimpleNamespace(failed=False, longrepr="ElementNotFoundError"), False),
            (SimpleNamespace(failed=True, longrepr=None), False),
            (SimpleNamespace(failed=True, longrepr="ordinary failure"), False),
            (
                SimpleNamespace(
                    failed=True,
                    longrepr="AssertionError: ElementNotFoundError was expected",
                    _dolphin_transient_failure=False,
                ),
                False,
            ),
            (
                SimpleNamespace(
                    failed=True,
                    longrepr="ElementNotFoundError: gone",
                    _dolphin_transient_failure=True,
                ),
                True,
            ),
            (
                SimpleNamespace(
                    failed=True,
                    longrepr="WaitTimeoutError: timed out",
                    _dolphin_transient_failure=True,
                ),
                True,
            ),
        ],
    )
    def test_transient_failure_detection(self, report, expected):
        assert plugin._is_transient_failure(report) is expected

    def test_report_hook_records_only_a_serializable_transient_flag(self, monkeypatch):
        monkeypatch.setattr(plugin, "_collect_artifacts", lambda *_args: None)
        item = _Item(Path("."))
        call = SimpleNamespace(
            when="call", excinfo=SimpleNamespace(value=ElementNotFoundError("gone"))
        )
        hook = plugin.pytest_runtest_makereport(item, call)
        next(hook)
        report = SimpleNamespace(failed=True, longrepr="ElementNotFoundError: gone")

        with pytest.raises(StopIteration):
            hook.send(SimpleNamespace(get_result=lambda: report))

        assert getattr(report, plugin._DOLPHIN_TRANSIENT_ATTR) is True
        assert isinstance(getattr(report, plugin._DOLPHIN_TRANSIENT_ATTR), bool)
        assert plugin._is_transient_failure(report) is True

    def test_report_hook_recognises_transient_exception_subclasses(self, monkeypatch):
        class DerivedElementNotFoundError(ElementNotFoundError):
            pass

        monkeypatch.setattr(plugin, "_collect_artifacts", lambda *_args: None)
        item = _Item(Path("."))
        call = SimpleNamespace(
            when="call", excinfo=SimpleNamespace(value=DerivedElementNotFoundError("gone"))
        )
        hook = plugin.pytest_runtest_makereport(item, call)
        next(hook)
        report = SimpleNamespace(failed=True, longrepr="gone")

        with pytest.raises(StopIteration):
            hook.send(SimpleNamespace(get_result=lambda: report))

        assert getattr(report, plugin._DOLPHIN_TRANSIENT_ATTR) is True

    def test_retry_decision_requires_remaining_attempt_and_transient_failure(self):
        item = _Item(Path("."))
        report = SimpleNamespace(
            failed=True,
            longrepr="WaitTimeoutError",
            _dolphin_transient_failure=True,
        )
        assert plugin._attempt_will_retry(item, report) is False
        item.stash[plugin._RETRY_MAX_KEY] = 2
        item.stash[plugin._RETRY_ATTEMPT_KEY] = 0
        assert plugin._attempt_will_retry(item, report) is True
        item.stash[plugin._RETRY_ATTEMPT_KEY] = 2
        assert plugin._attempt_will_retry(item, report) is False

    def test_retry_count_prefers_cli_over_global_config(self, monkeypatch):
        from dolphin_desktop import _config

        monkeypatch.setattr(_config, "get_retry_count", lambda: 8)
        config = SimpleNamespace(
            getoption=lambda name, default=None: 3 if name == "--dolphin-retry" else default
        )
        assert plugin._effective_retry_count(config) == 3

    def test_retry_count_uses_global_config_when_cli_is_absent(self, monkeypatch):
        from dolphin_desktop import _config

        monkeypatch.setattr(_config, "get_retry_count", lambda: 4)
        config = SimpleNamespace(getoption=lambda _name, default=None: default)
        assert plugin._effective_retry_count(config) == 4

    def test_runtest_protocol_delegates_when_retries_are_disabled(self, monkeypatch):
        monkeypatch.setattr(plugin, "_effective_retry_count", lambda _config: 0)
        item = SimpleNamespace(config=SimpleNamespace(), stash=pytest.Stash())
        assert plugin.pytest_runtest_protocol(item, None) is None

    def test_runtest_protocol_retries_then_publishes_only_final_reports(self, monkeypatch):
        reports = [
            [SimpleNamespace(when="setup"), SimpleNamespace(when="call")],
            [SimpleNamespace(when="setup"), SimpleNamespace(when="call")],
        ]
        transient = iter([True, False])
        calls: list[tuple] = []
        hooks = SimpleNamespace(
            pytest_runtest_logstart=lambda **kwargs: calls.append(("start", kwargs)),
            pytest_runtest_logreport=lambda **kwargs: calls.append(("report", kwargs["report"])),
            pytest_runtest_logfinish=lambda **kwargs: calls.append(("finish", kwargs)),
        )
        item = SimpleNamespace(
            config=SimpleNamespace(),
            stash=pytest.Stash(),
            nodeid="test-retry",
            location=("test.py", 1, "test_retry"),
            ihook=hooks,
        )
        monkeypatch.setattr(plugin, "_effective_retry_count", lambda _config: 1)
        monkeypatch.setattr(plugin, "_is_transient_failure", lambda _report: next(transient))
        monkeypatch.setattr(
            plugin.time,
            "sleep",
            lambda _seconds: (_ for _ in ()).throw(AssertionError("retry must not sleep")),
        )
        run_calls: list[tuple] = []

        def run(item_arg, log, nextitem):
            run_calls.append((item_arg, log, nextitem))
            return reports.pop(0)

        from _pytest import runner

        monkeypatch.setattr(runner, "runtestprotocol", run)
        assert plugin.pytest_runtest_protocol(item, "next") is True

        assert len(run_calls) == 2
        assert all(call[1] is False for call in run_calls)
        assert [entry[0] for entry in calls if entry[0] == "report"] == ["report", "report"]
        assert calls[0][0] == "start"
        assert calls[-1][0] == "finish"
        assert ("sleep",) not in calls
        assert item.stash[plugin._RETRY_MAX_KEY] == 1
        assert item.stash[plugin._RETRY_ATTEMPT_KEY] == 1

    def test_runtest_protocol_stops_on_nontransient_failure_and_handles_no_call_report(
        self, monkeypatch
    ):
        hooks = SimpleNamespace(
            pytest_runtest_logstart=lambda **_kwargs: None,
            pytest_runtest_logreport=lambda **_kwargs: None,
            pytest_runtest_logfinish=lambda **_kwargs: None,
        )
        item = SimpleNamespace(
            config=SimpleNamespace(),
            stash=pytest.Stash(),
            nodeid="test-no-call",
            location=(),
            ihook=hooks,
        )
        monkeypatch.setattr(plugin, "_effective_retry_count", lambda _config: 3)
        monkeypatch.setattr(plugin, "_is_transient_failure", lambda _report: False)
        from _pytest import runner

        run_count = 0

        def run(*_args, **_kwargs):
            nonlocal run_count
            run_count += 1
            return []

        monkeypatch.setattr(runner, "runtestprotocol", run)
        assert plugin.pytest_runtest_protocol(item, None) is True
        assert run_count == 1

    def test_runtest_protocol_publishes_last_retry_when_all_attempts_are_transient(
        self, monkeypatch
    ):
        published: list = []
        hooks = SimpleNamespace(
            pytest_runtest_logstart=lambda **_kwargs: None,
            pytest_runtest_logreport=lambda **kwargs: published.append(kwargs["report"]),
            pytest_runtest_logfinish=lambda **_kwargs: None,
        )
        item = SimpleNamespace(
            config=SimpleNamespace(),
            stash=pytest.Stash(),
            nodeid="test-last-retry",
            location=(),
            ihook=hooks,
        )
        monkeypatch.setattr(plugin, "_effective_retry_count", lambda _config: 1)
        monkeypatch.setattr(plugin, "_is_transient_failure", lambda _report: True)
        from _pytest import runner

        monkeypatch.setattr(
            runner,
            "runtestprotocol",
            lambda *_args, **_kwargs: [SimpleNamespace(when="call")],
        )
        assert plugin.pytest_runtest_protocol(item, None) is True
        assert len(published) == 1

    def test_runtest_protocol_finishes_if_attempt_iterator_is_empty(self, monkeypatch):
        hooks = SimpleNamespace(
            pytest_runtest_logstart=lambda **_kwargs: None,
            pytest_runtest_logreport=lambda **_kwargs: None,
            pytest_runtest_logfinish=lambda **_kwargs: None,
        )
        item = SimpleNamespace(
            config=SimpleNamespace(),
            stash=pytest.Stash(),
            nodeid="test-empty-attempts",
            location=(),
            ihook=hooks,
        )
        monkeypatch.setattr(plugin, "_effective_retry_count", lambda _config: 1)
        monkeypatch.setattr(plugin, "range", lambda _count: [], raising=False)
        assert plugin.pytest_runtest_protocol(item, None) is True

    def test_configure_registers_both_markers(self):
        lines: list[tuple[str, str]] = []
        config = SimpleNamespace(addinivalue_line=lambda key, value: lines.append((key, value)))
        plugin.pytest_configure(config)
        assert [key for key, _value in lines] == ["markers", "markers"]
        assert "dolphin(timeout" in lines[0][1]
        assert "dolphin_headless" in lines[1][1]

    def test_addoption_registers_all_dolphin_options(self):
        added: list[tuple[tuple, dict]] = []

        class Group:
            def addoption(self, *args, **kwargs):
                added.append((args, kwargs))

        parser = SimpleNamespace(getgroup=lambda name, description: Group())
        plugin.pytest_addoption(parser)
        names = [args[0] for args, _kwargs in added]
        assert names == [
            "--dolphin-backend",
            "--dolphin-timeout",
            "--dolphin-screenshot-on-fail",
            "--dolphin-headless",
            "--dolphin-trace",
            "--dolphin-trace-dir",
            "--dolphin-video",
            "--dolphin-video-dir",
            "--dolphin-html",
            "--dolphin-desktop-log-level",
            "--dolphin-retry",
        ]
        assert added[0][1]["default"] == "uia"
        assert added[1][1]["type"] is plugin._cli_timeout
        assert added[6][1]["choices"] == ["off", "keepfailedonly", "keepall"]
        assert added[10][1]["type"] is plugin._cli_retry


class TestFixtures:
    @pytest.mark.parametrize(
        ("value", "expected"),
        (("0", 0.0), ("1.5", 1.5), ("-0", -0.0)),
    )
    def test_cli_timeout_parser_accepts_finite_non_negative_values(self, value, expected):
        assert plugin._cli_timeout(value) == expected

    @pytest.mark.parametrize("value", ("nan", "inf", "-inf", "-1", "not-a-number"))
    def test_cli_timeout_parser_rejects_invalid_values(self, value):
        with pytest.raises(plugin.ArgumentTypeError, match="timeout"):
            plugin._cli_timeout(value)

    @pytest.mark.parametrize(("value", "expected"), (("0", 0), ("3", 3)))
    def test_cli_retry_parser_accepts_non_negative_integers(self, value, expected):
        assert plugin._cli_retry(value) == expected

    @pytest.mark.parametrize("value", ("-1", "1.5", "not-an-integer"))
    def test_cli_retry_parser_rejects_invalid_values(self, value):
        with pytest.raises(plugin.ArgumentTypeError, match="retry"):
            plugin._cli_retry(value)

    def test_session_fixtures_resolve_cli_env_and_defaults(self, monkeypatch, tmp_path):
        request = SimpleNamespace(
            config=_Options(tmp_path, **{"--dolphin-backend": "win32", "--dolphin-timeout": 2.5})
        )
        assert plugin.dolphin_backend.__wrapped__(request) == "win32"
        assert plugin.dolphin_timeout.__wrapped__(request) == 2.5

        no_cli = SimpleNamespace(config=_Options(tmp_path))
        monkeypatch.setenv("DOLPHIN_TIMEOUT", "3.75")
        assert plugin.dolphin_timeout.__wrapped__(no_cli) == 3.75
        monkeypatch.delenv("DOLPHIN_TIMEOUT")
        assert plugin.dolphin_timeout.__wrapped__(no_cli) == 10.0

    def test_session_config_applies_every_cli_override(self, monkeypatch, tmp_path):
        from dolphin_desktop import _config

        defaults = {}
        monkeypatch.setattr(_config, "_defaults", defaults)
        calls: list[str] = []
        monkeypatch.setattr(
            "dolphin_desktop._logging.apply_log_level", lambda: calls.append("level")
        )
        monkeypatch.setattr(
            "dolphin_desktop._logging.install_redaction", lambda: calls.append("redact")
        )
        monkeypatch.setattr("dolphin_desktop._telemetry.init", lambda: calls.append("telemetry"))
        options = {
            "--dolphin-trace": "always",
            "--dolphin-video": "keepall",
            "--dolphin-desktop-log-level": "DEBUG",
            "--dolphin-retry": 2,
        }
        request = SimpleNamespace(config=_Options(tmp_path, **options))
        plugin._dolphin_apply_session_config.__wrapped__(4.5, request)
        assert defaults == {
            "timeout": 4.5,
            "trace_mode": "always",
            "video_mode": "keepall",
            "log_level": "DEBUG",
            "retry_count": 2,
        }
        assert calls == ["level", "redact", "telemetry"]

    @pytest.mark.parametrize(
        ("cli", "env", "expected"),
        [(True, None, True), (False, "1", True), (False, "0", False), (False, None, False)],
    )
    def test_headless_fixture_resolves_cli_and_env(self, monkeypatch, tmp_path, cli, env, expected):
        request = SimpleNamespace(config=_Options(tmp_path, **{"--dolphin-headless": cli}))
        if env is None:
            monkeypatch.delenv("DOLPHIN_HEADLESS", raising=False)
        else:
            monkeypatch.setenv("DOLPHIN_HEADLESS", env)
        assert plugin.dolphin_headless.__wrapped__(request) is expected

    def test_desktop_fixture_passes_backend_and_hidden_flag(self, monkeypatch):
        created: list[tuple] = []

        class FakeDesktop:
            def __init__(self, **kwargs):
                created.append(kwargs)

        monkeypatch.setattr(plugin, "Desktop", FakeDesktop)
        assert isinstance(plugin.desktop.__wrapped__("uia", True), FakeDesktop)
        assert isinstance(plugin.desktop.__wrapped__("win32", False), FakeDesktop)
        assert created == [{"backend": "uia", "hidden": True}, {"backend": "win32", "hidden": None}]

    def test_marker_config_without_marker_is_a_noop(self, tmp_path):
        request = _request(tmp_path)
        generator = plugin._dolphin_marker_config.__wrapped__(request)
        next(generator)
        generator.close()

    def test_marker_config_skips_headless_test_before_mutating_defaults(
        self, monkeypatch, tmp_path
    ):
        from dolphin_desktop import _config

        old = dict(_config._defaults)
        request = _request(tmp_path, **{"--dolphin-headless": False})
        request.node.get_closest_marker = lambda _name: SimpleNamespace(kwargs={"headless": True})
        with pytest.raises(pytest.skip.Exception):
            next(plugin._dolphin_marker_config.__wrapped__(request))
        assert _config._defaults == old

    def test_marker_config_applies_and_restores_timeout_and_video(self, monkeypatch, tmp_path):
        from dolphin_desktop import _config

        monkeypatch.delenv("DOLPHIN_HEADLESS", raising=False)
        _config._defaults["timeout"] = 10.0
        _config._defaults["video_mode"] = "off"
        request = _request(tmp_path, **{"--dolphin-headless": False})
        request.node.get_closest_marker = lambda _name: SimpleNamespace(
            kwargs={"timeout": "1.25", "video_mode": "keepall", "headless": False}
        )
        generator = plugin._dolphin_marker_config.__wrapped__(request)
        next(generator)
        assert _config._defaults["timeout"] == 1.25
        assert _config._defaults["video_mode"] == "keepall"
        generator.close()
        assert _config._defaults["timeout"] == 10.0
        assert _config._defaults["video_mode"] == "off"

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        (
            ({"timeout": "not-a-number"}, "timeout"),
            ({"timeout": "nan"}, "timeout"),
            ({"timeout": -1}, "timeout"),
            ({"video_mode": "invalid-video"}, "video_mode"),
            ({"headless": "yes"}, "headless"),
        ),
    )
    def test_marker_config_rejects_invalid_values_before_mutation(
        self, monkeypatch, tmp_path, kwargs, message
    ):
        from dolphin_desktop import _config

        monkeypatch.setattr(_config, "_defaults", {"timeout": 10.0, "video_mode": "off"})
        request = _request(tmp_path, **{"--dolphin-headless": False})
        request.node.get_closest_marker = lambda _name: SimpleNamespace(kwargs=kwargs)
        with pytest.raises(pytest.UsageError, match=message):
            next(plugin._dolphin_marker_config.__wrapped__(request))
        assert _config._defaults == {"timeout": 10.0, "video_mode": "off"}

    def test_marker_config_covers_active_headless_and_absent_marker_options(
        self, monkeypatch, tmp_path
    ):
        from dolphin_desktop import _config

        monkeypatch.setattr(_config, "_defaults", {})
        request = _request(tmp_path, **{"--dolphin-headless": True})
        request.node.get_closest_marker = lambda _name: SimpleNamespace(kwargs={"headless": True})
        generator = plugin._dolphin_marker_config.__wrapped__(request)
        next(generator)
        with pytest.raises(StopIteration):
            next(generator)


class TestTraceAndVideoFixtures:
    def test_trace_fixture_is_noop_when_disabled(self, monkeypatch, tmp_path):
        from dolphin_desktop import _config

        monkeypatch.setattr(_config, "get_trace_mode", lambda: "off")
        request = _request(tmp_path)
        generator = plugin._dolphin_trace.__wrapped__(request)
        next(generator)
        with pytest.raises(StopIteration):
            next(generator)
        assert request.node.stash.get(plugin._TRACE_SESSION_KEY, None) is None

    def test_trace_fixture_logs_and_continues_when_store_cannot_open(
        self, monkeypatch, tmp_path, caplog
    ):
        from dolphin_desktop import _config, _trace

        monkeypatch.setattr(_config, "get_trace_mode", lambda: "always")
        monkeypatch.setattr(
            _trace, "TraceSession", lambda **_kwargs: (_ for _ in ()).throw(OSError("locked"))
        )
        request = _request(tmp_path, **{"--dolphin-trace-dir": "traces"})
        with caplog.at_level("WARNING", logger="dolphin_desktop.plugin"):
            generator = plugin._dolphin_trace.__wrapped__(request)
            next(generator)
            with pytest.raises(StopIteration):
                next(generator)
        assert "tracing disabled" in caplog.text

    def test_trace_fixture_sets_current_session_and_closes_it(self, monkeypatch, tmp_path):
        from dolphin_desktop import _config, _trace

        monkeypatch.setattr(_config, "get_trace_mode", lambda: "always")
        current: list = []

        class Session:
            def __init__(self, **kwargs):
                self.run_dir = kwargs["run_dir"]
                self.closed = 0

            def close_without_finish(self):
                self.closed += 1

        monkeypatch.setattr(_trace, "TraceSession", Session)
        monkeypatch.setattr(_trace, "set_current_session", lambda value: current.append(value))
        request = _request(tmp_path, **{"--dolphin-trace-dir": "traces"})
        generator = plugin._dolphin_trace.__wrapped__(request)
        next(generator)
        session = request.node.stash[plugin._TRACE_SESSION_KEY]
        assert current == [session]
        generator.close()
        assert current[-1] is None
        assert session.closed == 1

    def test_video_fixture_is_noop_when_disabled(self, monkeypatch, tmp_path):
        from dolphin_desktop import _config, _video

        monkeypatch.setattr(_config, "get_video_mode", lambda: "off")
        monkeypatch.setattr(_video, "VideoRecorder", lambda **_kwargs: pytest.fail("not created"))
        request = _request(tmp_path)
        generator = plugin._dolphin_video.__wrapped__(request)
        next(generator)
        with pytest.raises(StopIteration):
            next(generator)

    def test_video_fixture_exhausts_start_error_path(self, monkeypatch, tmp_path):
        from dolphin_desktop import _config, _video

        monkeypatch.setattr(_config, "get_video_mode", lambda: "keepall")

        class Recorder:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                raise OSError("capture unavailable")

        monkeypatch.setattr(_video, "VideoRecorder", Recorder)
        monkeypatch.setattr(_video, "find_ffmpeg", lambda: None)
        generator = plugin._dolphin_video.__wrapped__(_request(tmp_path))
        next(generator)
        with pytest.raises(StopIteration):
            next(generator)

    def test_video_fixture_stops_and_discards_recording_on_fallback_cleanup(
        self, monkeypatch, tmp_path
    ):
        from dolphin_desktop import _config, _video

        monkeypatch.setattr(_config, "get_video_mode", lambda: "keepall")
        events: list[str] = []

        class Recorder:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                events.append("start")

            def stop(self):
                events.append("stop")

            def discard(self):
                events.append("discard")

        monkeypatch.setattr(_video, "VideoRecorder", Recorder)
        request = _request(tmp_path)
        generator = plugin._dolphin_video.__wrapped__(request)
        next(generator)
        assert request.node.stash.get(plugin._VIDEO_RECORDER_KEY, None) is not None
        generator.close()
        assert events == ["start", "stop", "discard"]
        assert request.node.stash.get(plugin._VIDEO_RECORDER_KEY, None) is None

    def test_video_fixture_ignores_keyerror_when_fallback_stash_was_already_cleared(
        self, monkeypatch, tmp_path
    ):
        from dolphin_desktop import _config, _video

        monkeypatch.setattr(_config, "get_video_mode", lambda: "keepall")

        class Stash:
            def __init__(self):
                self.value = None

            def __setitem__(self, _key, value):
                self.value = value

            def get(self, _key, default=None):
                return self.value if self.value is not None else default

            def __delitem__(self, _key):
                raise KeyError

        class Recorder:
            def __init__(self, **_kwargs):
                pass

            def start(self):
                pass

            def stop(self):
                pass

            def discard(self):
                pass

        monkeypatch.setattr(_video, "VideoRecorder", Recorder)
        request = _request(tmp_path)
        request.node.stash = Stash()
        generator = plugin._dolphin_video.__wrapped__(request)
        next(generator)
        generator.close()

    def test_launch_fixture_tracks_apps_and_kills_all_even_after_one_error(self, tmp_path):
        events: list[str] = []

        class App:
            def __init__(self, name: str, explodes: bool = False):
                self.name = name
                self.explodes = explodes

            def kill(self):
                events.append(self.name)
                if self.explodes:
                    raise RuntimeError("already gone")

        class Desktop:
            def launch(self, command, **kwargs):
                return App(command, kwargs.get("explodes", False))

        generator = plugin.launch.__wrapped__(Desktop())
        launch = next(generator)
        assert launch("first.exe", explodes=True).name == "first.exe"
        assert launch("second.exe").name == "second.exe"
        with pytest.raises(StopIteration):
            next(generator)
        assert events == ["first.exe", "second.exe"]


class TestAllureAndVideoHelpers:
    def test_allure_availability_returns_false_when_import_fails(self, monkeypatch):
        real_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name == "allure":
                raise ImportError("allure is not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked_import)
        assert plugin._is_allure_available() is False

    def test_allure_availability_and_screenshot_attachment(self, monkeypatch, tmp_path):
        calls: list[tuple] = []
        monkeypatch.setitem(sys.modules, "allure", _fake_allure(calls))
        assert plugin._is_allure_available() is True
        path = tmp_path / "screen.png"
        path.write_bytes(b"png")
        plugin._attach_allure_screenshot(path)
        assert calls[0][0][0] == b"png"
        assert calls[0][1]["name"] == "Screenshot"

    def test_allure_trace_prefers_html(self, monkeypatch, tmp_path):
        calls: list[tuple] = []
        monkeypatch.setitem(sys.modules, "allure", _fake_allure(calls))
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "trace.html").write_text("<html>trace</html>", encoding="utf-8")
        plugin._attach_allure_trace(run_dir)
        assert calls[0][0][0] == b"<html>trace</html>"
        assert calls[0][1]["attachment_type"] == "html"

    def test_allure_trace_zips_run_when_no_html_exists(self, monkeypatch, tmp_path):
        calls: list[tuple] = []
        monkeypatch.setitem(sys.modules, "allure", _fake_allure(calls))
        run_dir = tmp_path / "run"
        (run_dir / "nested").mkdir(parents=True)
        (run_dir / "nested" / "trace.db").write_bytes(b"db")
        plugin._attach_allure_trace(run_dir)
        zip_path = tmp_path / "run.zip"
        assert zip_path.exists()
        with zipfile.ZipFile(zip_path) as archive:
            assert archive.namelist() == ["run/nested/trace.db"]
        assert calls[0][1]["attachment_type"] == "zip"

    def test_allure_trace_ignores_missing_directory(self, monkeypatch, tmp_path):
        calls: list[tuple] = []
        monkeypatch.setitem(sys.modules, "allure", _fake_allure(calls))
        plugin._attach_allure_trace(tmp_path / "missing")
        assert calls == []

    def test_allure_text_and_video_attachments(self, monkeypatch, tmp_path):
        calls: list[tuple] = []
        file_calls: list[tuple] = []
        monkeypatch.setitem(sys.modules, "allure", _fake_allure(calls, file_calls))
        plugin._attach_allure_text("stdout", "stdout")
        video = tmp_path / "video.mp4"
        video.write_bytes(b"mp4")
        plugin._attach_allure_video(video)
        assert calls[0][0][0] == "stdout"
        assert file_calls[0][0][0] == str(video)
        assert file_calls[0][1]["attachment_type"] == "mp4"

    def test_allure_helpers_swallow_attachment_errors(self, monkeypatch, tmp_path):
        broken = types.ModuleType("allure")
        broken.attachment_type = SimpleNamespace(
            PNG="png", HTML="html", ZIP="zip", TEXT="text", MP4="mp4"
        )
        broken.attach = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broken"))
        monkeypatch.setitem(sys.modules, "allure", broken)
        path = tmp_path / "a.png"
        path.write_bytes(b"x")
        plugin._attach_allure_screenshot(path)
        plugin._attach_allure_trace(tmp_path)
        plugin._attach_allure_text("x", "x")

    def test_screenshot_capture_returns_none_when_pillow_import_fails(self, monkeypatch, tmp_path):
        real_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name == "PIL":
                raise ImportError("Pillow is not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", blocked_import)
        assert plugin._capture_failure_screenshot(_Item(tmp_path)) is None

    def test_handle_video_returns_none_without_recorder(self, tmp_path):
        item = _Item(tmp_path)
        assert plugin._handle_video(item, _report(failed=True), "call") is None

    def test_handle_video_discards_successful_recording_that_should_not_be_kept(
        self, monkeypatch, tmp_path
    ):
        from dolphin_desktop import _config

        monkeypatch.setattr(_config, "get_video_mode", lambda: "keepfailedonly")
        events: list[str] = []

        class Recorder:
            def stop(self):
                events.append("stop")

            def discard(self):
                events.append("discard")

        item = _Item(tmp_path)
        item.stash[plugin._VIDEO_RECORDER_KEY] = Recorder()
        assert plugin._handle_video(item, _report(failed=False), "call") is None
        assert events == ["stop", "discard"]
        assert item.stash.get(plugin._VIDEO_RECORDER_KEY, None) is None

    def test_handle_video_encodes_failed_recording_and_adds_report_data(
        self, monkeypatch, tmp_path
    ):
        from dolphin_desktop import _config

        monkeypatch.setattr(_config, "get_video_mode", lambda: "keepfailedonly")
        events: list[str] = []

        class Recorder:
            def stop(self):
                events.append("stop")

            def encode(self, path):
                events.append("encode")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"video")

            def discard(self):
                events.append("discard")

        attached: list[Path] = []
        monkeypatch.setattr(plugin, "_attach_allure_video", attached.append)
        item = _Item(tmp_path, **{"--dolphin-video-dir": "videos"})
        item.stash[plugin._VIDEO_RECORDER_KEY] = Recorder()
        path = plugin._handle_video(item, _report(failed=True), "setup")
        assert path is not None and path.exists()
        assert events == ["stop", "encode", "discard"]
        assert attached == [path]
        assert item.sections[0][0:2] == ("setup", "dolphin video")

    def test_handle_video_keeps_all_on_pass_and_survives_delete_keyerror(
        self, monkeypatch, tmp_path
    ):
        from dolphin_desktop import _config

        monkeypatch.setattr(_config, "get_video_mode", lambda: "keepall")

        class Stash:
            def __init__(self, recorder):
                self.recorder = recorder

            def get(self, _key, _default=None):
                return self.recorder

            def __delitem__(self, _key):
                raise KeyError

        class Recorder:
            def stop(self):
                pass

            def encode(self, path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"video")

            def discard(self):
                pass

        item = _Item(tmp_path)
        item.stash = Stash(Recorder())
        monkeypatch.setattr(plugin, "_attach_allure_video", lambda _path: None)
        assert plugin._handle_video(item, _report(failed=False), "call") is not None


class TestReportCollection:
    def test_reports_with_failures_are_serializable_under_xdist(self, tmp_path):
        test_file = tmp_path / "test_xdist_report_serialization.py"
        test_file.write_text(
            """
from dolphin_desktop import ElementNotFoundError


def test_transient_failure_report():
    raise ElementNotFoundError("element disappeared")


def test_regular_failure_report():
    raise AssertionError("ordinary failure")
""",
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-n",
                "2",
                "-q",
                "-p",
                "dolphin_desktop.pytest_plugin",
                "--dolphin-trace=off",
                "--dolphin-video=off",
                "--dolphin-html",
                str(tmp_path / "dolphin-report.html"),
                "-c",
                str(REPO_ROOT / "pyproject.toml"),
                str(test_file),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = result.stdout + result.stderr

        assert result.returncode == 1, output
        assert "2 failed" in output
        assert "DumpError" not in output
        assert "can't serialize" not in output

    def test_makereport_calls_collector_after_yield(self, monkeypatch):
        seen: list[tuple] = []
        monkeypatch.setattr(plugin, "_collect_artifacts", lambda *args: seen.append(args))
        item = SimpleNamespace(nodeid="test")
        call = SimpleNamespace(when="call")
        hook = plugin.pytest_runtest_makereport(item, call)
        next(hook)
        report = _report(failed=False)
        with pytest.raises(StopIteration):
            hook.send(SimpleNamespace(get_result=lambda: report))
        assert seen == [(item, call, report)]

    def test_collect_suppresses_all_artifacts_for_retrying_attempt(self, monkeypatch, tmp_path):
        plugin._session_reports.clear()
        session = SimpleNamespace(
            close_without_finish=lambda: setattr(session, "closed", True), closed=False
        )
        item = _Item(tmp_path)
        item.stash[plugin._TRACE_SESSION_KEY] = session
        monkeypatch.setattr(plugin, "_attempt_will_retry", lambda _item, _report: True)
        plugin._collect_artifacts(item, SimpleNamespace(when="call"), _report(failed=True))
        assert session.closed is True
        assert plugin._session_reports == []

    def test_collect_suppresses_retry_without_a_trace_session(self, monkeypatch, tmp_path):
        plugin._session_reports.clear()
        monkeypatch.setattr(plugin, "_attempt_will_retry", lambda _item, _report: True)
        plugin._collect_artifacts(
            _Item(tmp_path), SimpleNamespace(when="call"), _report(failed=True)
        )
        assert plugin._session_reports == []

    def test_collect_handles_setup_pass_without_finalising_anything(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            plugin, "_record_teardown_failure", lambda *_args: pytest.fail("not teardown")
        )
        plugin._collect_artifacts(
            _Item(tmp_path), SimpleNamespace(when="setup"), _report(failed=False)
        )

    def test_collect_handles_teardown_failure(self, monkeypatch, tmp_path):
        plugin._session_reports.clear()
        item = _Item(tmp_path)
        plugin._collect_artifacts(
            item, SimpleNamespace(when="teardown"), _report(failed=True, longrepr="cleanup")
        )
        assert item.sections[0][0:2] == ("teardown", "dolphin")
        assert plugin._session_reports[0]["outcome"] == "error"
        plugin._session_reports.clear()

    def test_collect_failed_call_finalises_trace_screenshot_video_and_allure_text(
        self, monkeypatch, tmp_path
    ):
        from dolphin_desktop import _trace

        plugin._session_reports.clear()
        item = _Item(tmp_path, **{"--dolphin-screenshot-on-fail": True})
        trace = SimpleNamespace(
            run_dir=tmp_path / "trace",
            finish=lambda *args, **kwargs: setattr(trace, "finished", (args, kwargs)),
        )
        item.stash[plugin._TRACE_SESSION_KEY] = trace
        screenshot = tmp_path / "shot.png"
        video = tmp_path / "video.mp4"
        monkeypatch.setattr(plugin, "_capture_failure_screenshot", lambda *_args: screenshot)
        monkeypatch.setattr(plugin, "_handle_video", lambda *_args: video)
        monkeypatch.setattr(
            plugin, "_attach_allure_trace", lambda path: setattr(trace, "attached", path)
        )
        text_calls: list[tuple] = []
        monkeypatch.setattr(plugin, "_attach_allure_text", lambda *args: text_calls.append(args))
        monkeypatch.setattr(_trace, "TraceSession", object)
        report = _report(
            failed=True,
            longrepr="password=secret",
            capstdout="out",
            capstderr="err",
        )
        plugin._collect_artifacts(item, SimpleNamespace(when="call"), report)
        assert trace.finished[0] == ("failed",)
        assert trace.attached == trace.run_dir
        assert text_calls == [("out", "stdout"), ("err", "stderr")]
        assert [name for name, _value in report.user_properties] == [
            "dolphin_screenshot",
            "dolphin_trace",
            "dolphin_video",
        ]
        assert plugin._session_reports[0]["screenshot"] == str(screenshot)
        plugin._session_reports.clear()

    def test_collect_setup_failure_uses_setup_phase_and_redacts_error(self, monkeypatch, tmp_path):
        plugin._session_reports.clear()
        item = _Item(tmp_path)
        trace = SimpleNamespace(
            run_dir=tmp_path / "trace", finish=lambda *args, **kwargs: setattr(trace, "args", args)
        )
        item.stash[plugin._TRACE_SESSION_KEY] = trace
        monkeypatch.setattr(plugin, "_handle_video", lambda *_args: None)
        report = _report(failed=True, longrepr="setup failed")
        plugin._collect_artifacts(item, SimpleNamespace(when="setup"), report)
        assert trace.args[0] == "failed"
        assert item.sections[0][0:2] == ("setup", "dolphin trace")
        assert plugin._session_reports[0]["outcome"] == "failed"
        plugin._session_reports.clear()

    def test_collect_pass_finalises_trace_and_accumulates_pass(self, monkeypatch, tmp_path):
        plugin._session_reports.clear()
        item = _Item(tmp_path)
        trace = SimpleNamespace(
            run_dir=tmp_path / "trace", finish=lambda *args: setattr(trace, "outcome", args)
        )
        item.stash[plugin._TRACE_SESSION_KEY] = trace
        monkeypatch.setattr(plugin, "_handle_video", lambda *_args: None)
        plugin._collect_artifacts(item, SimpleNamespace(when="call"), _report(failed=False))
        assert trace.outcome == ("passed",)
        assert plugin._session_reports[0]["outcome"] == "passed"
        plugin._session_reports.clear()

    def test_collect_call_without_trace_session_still_accumulates_report(
        self, monkeypatch, tmp_path
    ):
        plugin._session_reports.clear()
        monkeypatch.setattr(plugin, "_handle_video", lambda *_args: None)
        plugin._collect_artifacts(
            _Item(tmp_path), SimpleNamespace(when="call"), _report(failed=False)
        )
        assert plugin._session_reports[0]["trace"] is None
        plugin._session_reports.clear()

    def test_record_teardown_failure_amends_latest_matching_row(self, tmp_path):
        plugin._session_reports[:] = [
            {"nodeid": "test", "outcome": "passed"},
            {"nodeid": "other", "outcome": "passed"},
        ]
        item = _Item(tmp_path, "test")
        plugin._record_teardown_failure(item, _report(failed=True, longrepr="teardown boom"))
        assert plugin._session_reports[0]["outcome"] == "error"
        assert item.sections[0][1] == "dolphin"
        plugin._session_reports.clear()

    def test_record_teardown_failure_creates_row_and_uses_default_message(self, tmp_path):
        plugin._session_reports.clear()
        item = _Item(tmp_path, "new-test")
        plugin._record_teardown_failure(item, _report(failed=True, longrepr=None))
        assert plugin._session_reports == [
            {
                "nodeid": "new-test",
                "outcome": "error",
                "duration": 0.25,
                "screenshot": None,
                "trace": None,
                "video": None,
                "teardown_error": "teardown failed",
            }
        ]
        plugin._session_reports.clear()


class TestSessionReports:
    def test_sessionfinish_returns_without_reports(self, monkeypatch):
        from dolphin_desktop import _application

        plugin._session_reports.clear()
        monkeypatch.setattr(_application, "_session_pids", set())
        plugin.pytest_sessionfinish(SimpleNamespace(config=SimpleNamespace()), 0)

    def test_sessionfinish_kills_orphans_and_writes_explicit_worker_report(
        self, monkeypatch, tmp_path
    ):
        from dolphin_desktop import _application

        plugin._session_reports[:] = [
            {
                "nodeid": "test",
                "outcome": "passed",
                "duration": 0.1,
                "screenshot": None,
                "trace": None,
                "video": None,
            }
        ]
        api_calls: list[tuple] = []
        monkeypatch.setitem(
            sys.modules,
            "win32api",
            types.SimpleNamespace(
                OpenProcess=lambda *args: api_calls.append(("open", *args)) or "h",
                TerminateProcess=lambda *args: api_calls.append(("terminate", *args)),
                CloseHandle=lambda *args: api_calls.append(("close", *args)),
            ),
        )
        monkeypatch.setitem(sys.modules, "win32con", types.SimpleNamespace(PROCESS_TERMINATE=9))
        session_pids = {88}
        live_pids = {88, 99}
        monkeypatch.setattr(_application, "_session_pids", session_pids)
        monkeypatch.setattr(_application, "_live_pids", live_pids)
        output_paths: list[Path] = []
        monkeypatch.setattr(plugin, "_generate_html_report", output_paths.append)
        monkeypatch.setattr(
            plugin, "_is_allure_available", lambda: pytest.fail("explicit path skips check")
        )
        explicit = tmp_path / "reports" / "summary.html"
        config = _Options(tmp_path, **{"--dolphin-html": str(explicit)})
        config.workerinput = {"workerid": "gw7"}
        plugin.pytest_sessionfinish(SimpleNamespace(config=config), 0)
        assert session_pids == set()
        assert live_pids == {99}
        assert api_calls[1] == ("terminate", "h", 1)
        assert output_paths == [tmp_path / "reports" / "summary-gw7.html"]
        plugin._session_reports.clear()

    def test_sessionfinish_uses_default_report_without_allure(self, monkeypatch, tmp_path):
        from dolphin_desktop import _application

        plugin._session_reports[:] = [
            {
                "nodeid": "test",
                "outcome": "passed",
                "duration": 0.1,
                "screenshot": None,
                "trace": None,
                "video": None,
            }
        ]
        monkeypatch.setattr(_application, "_session_pids", set())
        monkeypatch.setattr(plugin, "_is_allure_available", lambda: False)
        outputs: list[Path] = []
        monkeypatch.setattr(plugin, "_generate_html_report", outputs.append)
        config = _Options(tmp_path, **{"--dolphin-html": None})
        plugin.pytest_sessionfinish(SimpleNamespace(config=config), 0)
        assert outputs == [tmp_path / "dolphin-report.html"]
        plugin._session_reports.clear()

    def test_sessionfinish_skips_fallback_when_allure_is_available(self, monkeypatch, tmp_path):
        from dolphin_desktop import _application

        plugin._session_reports[:] = [
            {
                "nodeid": "test",
                "outcome": "passed",
                "duration": 0.1,
                "screenshot": None,
                "trace": None,
                "video": None,
            }
        ]
        monkeypatch.setattr(_application, "_session_pids", set())
        monkeypatch.setattr(plugin, "_is_allure_available", lambda: True)
        monkeypatch.setattr(
            plugin, "_generate_html_report", lambda _path: pytest.fail("not written")
        )
        config = _Options(tmp_path, **{"--dolphin-html": None})
        plugin.pytest_sessionfinish(SimpleNamespace(config=config), 0)
        plugin._session_reports.clear()

    def test_sessionfinish_always_discards_orphan_when_process_cleanup_raises(
        self, monkeypatch, tmp_path
    ):
        from dolphin_desktop import _application

        monkeypatch.setitem(
            sys.modules,
            "win32api",
            types.SimpleNamespace(
                OpenProcess=lambda *_args: (_ for _ in ()).throw(OSError("gone"))
            ),
        )
        monkeypatch.setitem(sys.modules, "win32con", types.SimpleNamespace(PROCESS_TERMINATE=9))
        pids = {77}
        live = {77}
        monkeypatch.setattr(_application, "_session_pids", pids)
        monkeypatch.setattr(_application, "_live_pids", live)
        plugin._session_reports.clear()
        plugin.pytest_sessionfinish(SimpleNamespace(config=SimpleNamespace()), 0)
        assert pids == set()
        assert live == set()

    def test_sessionfinish_uses_anchored_handle_after_pid_reuse(self, monkeypatch):
        from dolphin_desktop import _application

        calls: list[tuple] = []
        monkeypatch.setitem(
            sys.modules,
            "win32api",
            types.SimpleNamespace(
                OpenProcess=lambda *_args: pytest.fail("PID must not be reopened"),
                TerminateProcess=lambda *args: calls.append(("terminate", *args)),
                CloseHandle=lambda *args: calls.append(("close", *args)),
            ),
        )
        pids = {507}
        live = {507}
        handles = {507: "original-process-handle"}
        monkeypatch.setattr(_application, "_session_pids", pids)
        monkeypatch.setattr(_application, "_live_pids", live)
        monkeypatch.setattr(_application, "_owned_process_handles", handles)
        plugin._session_reports.clear()

        plugin.pytest_sessionfinish(SimpleNamespace(config=SimpleNamespace()), 0)

        assert calls == [
            ("terminate", "original-process-handle", 1),
            ("close", "original-process-handle"),
        ]
        assert pids == set()
        assert live == set()
        assert handles == {}


class TestHtmlReport:
    def test_generate_html_renders_all_statuses_and_artifact_links(self, tmp_path):
        plugin._session_reports[:] = [
            {
                "nodeid": "pass",
                "outcome": "passed",
                "duration": 1.0,
                "screenshot": None,
                "trace": None,
                "video": None,
            },
            {
                "nodeid": "fail",
                "outcome": "failed",
                "duration": 2.0,
                "screenshot": "shot.png",
                "trace": "trace.html",
                "video": "video.mp4",
            },
            {
                "nodeid": "skip",
                "outcome": "skipped",
                "duration": 0.0,
                "screenshot": None,
                "trace": None,
                "video": None,
            },
            {
                "nodeid": "error",
                "outcome": "error",
                "duration": 0.5,
                "screenshot": None,
                "trace": None,
                "video": None,
            },
            {
                "nodeid": "other",
                "outcome": "unknown",
                "screenshot": None,
                "trace": None,
                "video": None,
            },
        ]
        output = tmp_path / "nested" / "report.html"
        plugin._generate_html_report(output)
        html = output.read_text(encoding="utf-8")
        assert "Passed:</b> 1" in html
        assert "Failed:</b> 1" in html
        assert "Errors:</b> 1" in html
        assert "Skipped:</b> 1" in html
        assert "shot.png" in html and "trace.html" in html and "video.mp4" in html
        assert "UNKNOWN" in html
        plugin._session_reports.clear()


def test_pytest_plugin_artifact_names_are_filesystem_safe() -> None:
    from dolphin_desktop.pytest_plugin import _artifact_file_stem, _trace_run_dir_name

    assert ":" not in _artifact_file_stem("a::test[x/y]")
    assert ":" not in _trace_run_dir_name("a::test")
