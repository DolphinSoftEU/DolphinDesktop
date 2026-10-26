"""KAN-469: scan real Allure result/container JSON and captured attachments."""

from __future__ import annotations

import pytest
from allure_commons._hooks import AllureDeveloperHooks
from allure_commons.logger import AllureFileLogger
from allure_commons.model2 import Attachment, GlobalError, Globals, Parameter, StatusDetails
from allure_commons.model2 import TestAfterResult as AllureAfter
from allure_commons.model2 import TestBeforeResult as AllureBefore
from allure_commons.model2 import TestResult as AllureResult
from allure_commons.model2 import TestResultContainer as AllureContainer
from allure_commons.model2 import TestStepResult as AllureStep
from pluggy import PluginManager

from dolphin_desktop._allure import _PLUGIN_NAME, _AllureRedactor, _redact_item
from tests._allure_redaction import run_allure_case


@pytest.mark.parametrize("phase", ["success", "call", "setup", "teardown", "xfail"])
@pytest.mark.parametrize("style", ["pin", "marked"])
@pytest.mark.parametrize("order", ["dolphin-first", "allure-first", "entry-point"])
def test_real_allure_redacts_results_fixtures_steps_and_attachments(tmp_path, phase, style, order):
    case = run_allure_case(tmp_path, phase, style, order)
    assert case["exit_code"] == (0 if phase in {"success", "xfail"} else 1), case["terminal"]
    assert case["leaks"] == [], f"Canary leaked into Allure files: {case['leaks']}"
    assert case["canary"] not in case["terminal"]
    results = [value for name, value in case["documents"].items() if name.endswith("-result.json")]
    assert len(results) == 1
    result = results[0]
    assert result["status"] == (
        "passed" if phase == "success" else "skipped" if phase == "xfail" else "broken"
    )
    assert "operation" in result["name"]
    assert next(item for item in result["parameters"] if item["name"] == "pin")["value"] == "***"
    assert next(item for item in result["labels"] if item["name"] == "pin")["value"] == "***"
    if phase != "success":
        assert "RuntimeError" in result["statusDetails"]["message"]
        assert "operation" in result["statusDetails"]["message"]
        assert "***" in result["statusDetails"]["message"]
        assert "***" in result["statusDetails"]["trace"]
    if phase in {"setup", "teardown"}:
        fixtures = [
            fixture
            for name, container in case["documents"].items()
            if name.endswith("-container.json")
            for key in ("befores", "afters")
            for fixture in container.get(key, [])
            if fixture["name"].startswith("scenario") and fixture["status"] == "broken"
        ]
        assert fixtures, "Allure must retain the failing fixture diagnostics"
        for fixture in fixtures:
            assert "RuntimeError" in fixture["statusDetails"]["message"]
            assert "***" in fixture["statusDetails"]["message"]
            assert "scenario" in fixture["statusDetails"]["trace"]
            assert fixture["steps"][-1]["status"] == "broken"
            assert "***" in fixture["steps"][-1]["statusDetails"]["message"]
    attachments = "\n".join(case["attachments"].values())
    assert "setup stdout" in attachments and "setup stderr" in attachments
    assert "***" in attachments


@pytest.mark.parametrize("redactor_first", [False, True])
def test_models_are_redacted_before_real_file_serialization(tmp_path, redactor_first):
    manager = PluginManager("allure")
    manager.add_hookspecs(AllureDeveloperHooks)
    plugins = [_AllureRedactor(), AllureFileLogger(tmp_path)]
    if not redactor_first:
        plugins.reverse()
    for plugin in plugins:
        manager.register(plugin)
    manager.check_pending()
    canary = "KAN469_MODEL_CANARY_87b1"

    def details():
        return StatusDetails(message=f"RuntimeError: pin={canary}", trace=f"operation pin={canary}")

    result = AllureResult(
        uuid="stable-test-id",
        name="operation",
        status="broken",
        statusDetails=details(),
        steps=[AllureStep(name="nested operation", status="broken", statusDetails=details())],
        attachments=[
            Attachment(name="screenshot", source="stable-attachment.png", type="image/png")
        ],
        parameters=[Parameter(name="pin", value=canary), Parameter(name="attempts", value=2)],
    )
    container = AllureContainer(
        uuid="stable-container-id",
        children=[result.uuid],
        befores=[AllureBefore(name="setup operation", status="broken", statusDetails=details())],
        afters=[AllureAfter(name="teardown operation", status="broken", statusDetails=details())],
    )
    manager.hook.report_result(result=result)
    manager.hook.report_container(container=container)
    manager.hook.report_globals(
        globals_item=Globals(errors=[GlobalError(message=f"pin={canary}", trace=f"pin={canary}")])
    )
    documents = [path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json")]
    assert len(documents) == 3
    assert all(canary not in document and "***" in document for document in documents)
    assert "RuntimeError" in result.statusDetails.message
    assert result.status == "broken" and result.steps[0].name == "nested operation"
    assert result.parameters[0].value == "***" and result.parameters[1].value == 2
    assert container.children == [result.uuid] == ["stable-test-id"]
    assert result.attachments[0].source == "stable-attachment.png"


def test_structured_redaction_handles_nested_models_and_cycles():
    cyclic = []
    cyclic.append(cyclic)
    value = {
        "pin": 1234,
        "items": (Parameter(name="password", value="bare-canary"), "operation pin=CANARY_87b1"),
        "cycle": cyclic,
    }
    redacted = _redact_item(value)
    assert redacted["pin"] == "***"
    assert redacted["items"][0].value == "***"
    assert redacted["items"][1] == "operation pin=***"
    assert "***" in repr(redacted["cycle"])


def test_marked_values_cannot_change_allure_status_or_attachment_references(monkeypatch):
    from dolphin_desktop import _logging

    monkeypatch.setattr(_logging, "_marked_secrets", {"1234": None, "broken": None})
    result = AllureResult(
        uuid="1234-test-id",
        status="broken",
        name="operation pin=1234",
        attachments=[Attachment(name="broken operation", source="1234.png", type="image/png")],
    )
    container = AllureContainer(uuid="1234-container-id", children=[result.uuid])
    _redact_item(result)
    _redact_item(container)
    assert result.status == "broken" and result.uuid == "1234-test-id"
    assert result.name == "operation pin=***"
    assert result.attachments[0].name == "*** operation"
    assert result.attachments[0].source == "1234.png"
    assert container.children == [result.uuid]


def test_redaction_registration_is_idempotent_and_scoped_to_configuration(monkeypatch):
    import allure_commons

    from dolphin_desktop.pytest_plugin import _configure_allure_redaction

    manager = PluginManager("allure")
    manager.add_hookspecs(AllureDeveloperHooks)
    monkeypatch.setattr(allure_commons, "plugin_manager", manager)

    class Configuration:
        def __init__(self):
            self.cleanups = []

        def add_cleanup(self, callback):
            self.cleanups.append(callback)

    first, second = Configuration(), Configuration()
    _configure_allure_redaction(first)
    redactor = manager.get_plugin(_PLUGIN_NAME)
    assert isinstance(redactor, _AllureRedactor)
    _configure_allure_redaction(first)
    _configure_allure_redaction(second)
    assert manager.get_plugins() == {redactor}
    assert len(first.cleanups) == 1 and not second.cleanups
    first.cleanups.pop()()
    assert manager.get_plugin(_PLUGIN_NAME) is None
    _configure_allure_redaction(second)
    assert isinstance(manager.get_plugin(_PLUGIN_NAME), _AllureRedactor)
    second.cleanups.pop()()
    assert not manager.get_plugins()
