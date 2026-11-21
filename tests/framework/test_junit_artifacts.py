"""Exercise artifact properties through pytest's real JUnit publication lifecycle."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from textwrap import dedent
from xml.etree import ElementTree

import pytest
from PIL import Image


@pytest.mark.parametrize(
    ("scenario", "video_mode", "capture_error", "failed", "keep_video"),
    [
        ("fail", "keepall", False, True, True),
        ("pass", "keepall", False, False, True),
        ("fail", "keepfailedonly", False, True, True),
        ("pass", "keepfailedonly", False, False, False),
        ("setup", "keepall", False, True, True),
        ("retry_pass", "keepall", False, False, True),
        ("retry_pass", "keepfailedonly", False, False, False),
        ("retry_fail", "keepall", False, True, True),
        ("fail", "off", True, True, False),
    ],
)
def test_junit_retains_only_final_attempt_artifacts(
    tmp_path, monkeypatch, scenario, video_mode, capture_error, failed, keep_video
):
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        dedent(
            f"""
            import pytest
            from PIL import Image, ImageGrab
            from dolphin_desktop import _video

            def grab(**kwargs):
                assert kwargs == {{'all_screens': True}}
                if {capture_error!r}:
                    raise OSError('controlled capture failure')
                return Image.new('RGB', (16, 16), 'blue')

            ImageGrab.grab = grab

            class Recorder:
                def __init__(self, **kwargs): pass
                def start(self): pass
                def stop(self): pass
                def discard(self): pass
                def encode(self, path):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(b'controlled video payload')

            _video.VideoRecorder = Recorder

            @pytest.fixture
            def setup_check():
                if {scenario!r} == 'setup':
                    raise AssertionError('original setup failure')
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "test_sample.py").write_text(
        dedent(
            f"""
            from dolphin_desktop import ElementNotFoundError
            attempts = 0

            def test_sample(setup_check, record_property):
                global attempts
                attempts += 1
                if {scenario!r}.startswith('retry') and attempts == 1:
                    raise ElementNotFoundError('transient first attempt')
                record_property('custom', 'preserved')
                if {scenario!r} == 'retry_fail':
                    raise ElementNotFoundError('final transient failure')
                if {scenario!r} == 'fail':
                    raise AssertionError('original assertion failure')
            """
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:dolphin-desktop",
            "-p",
            "dolphin_desktop.pytest_plugin",
            "--dolphin-screenshot-on-fail",
            f"--dolphin-video={video_mode}",
            "--dolphin-trace=on-failure",
            f"--dolphin-retry={int(scenario.startswith('retry'))}",
            "--junitxml=result.xml",
            "-q",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode == int(failed), output
    root = ElementTree.parse(tmp_path / "result.xml").getroot()
    cases = root.findall(".//testcase")
    assert len(cases) == 1, output
    case = cases[0]
    properties = case.findall("properties/property")
    expected = set()
    if failed:
        expected.add("dolphin_trace")
    if failed and not capture_error:
        expected.add("dolphin_screenshot")
    if keep_video:
        expected.add("dolphin_video")
    artifacts = [p for p in properties if p.get("name", "").startswith("dolphin_")]
    assert {p.get("name") for p in artifacts} == expected
    assert len(artifacts) == len(expected)
    for prop in artifacts:
        path = Path(prop.attrib["value"])
        assert path.exists()
        if prop.get("name") == "dolphin_screenshot":
            with Image.open(path) as image:
                assert image.format == "PNG"
                image.verify()
    assert len(list(tmp_path.rglob("*.png"))) == int("dolphin_screenshot" in expected)
    assert len(list(tmp_path.rglob("*.mp4"))) == int(keep_video)
    if scenario != "setup":
        custom = [p.get("value") for p in properties if p.get("name") == "custom"]
        assert custom == ["preserved"]
    if failed:
        failure = case.find("error" if scenario == "setup" else "failure")
        assert failure is not None
        message = (
            "original setup failure"
            if scenario == "setup"
            else "final transient failure"
            if scenario == "retry_fail"
            else "original assertion failure"
        )
        assert message in (failure.text or "")
