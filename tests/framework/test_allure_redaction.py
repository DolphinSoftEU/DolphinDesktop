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


@pytest.mark.parametrize("shape", ["scalar", "dict", "nested"])
@pytest.mark.parametrize("fails", [False, True], ids=["success", "failure"])
@pytest.mark.parametrize("order", ["dolphin-first", "allure-first", "entry-point"])
def test_sensitive_parameter_leaves_are_redacted_in_real_captured_attachments(
    tmp_path, shape, fails, order
):
    expression, extract = {
        "scalar": ('os.environ["KAN469_CANARY"]', "pin"),
        "dict": ('{"code": os.environ["KAN469_CANARY"]}', 'pin["code"]'),
        "nested": ('{"codes": [{"code": os.environ["KAN469_CANARY"]}]}', 'pin["codes"][0]["code"]'),
    }[shape]
    source = (
        """
import os
import sys
import pytest

@pytest.fixture
def diagnostic(pin):
    print("setup stdout", EXTRACT)
    print("setup stderr", EXTRACT, file=sys.stderr)
    yield
    print("teardown stdout", EXTRACT)
    print("teardown stderr", EXTRACT, file=sys.stderr)

@pytest.mark.parametrize("pin", [VALUE], ids=lambda pin: EXTRACT)
def test_login(pin, diagnostic):
    print("Authenticating with", EXTRACT)
    print("call stderr", EXTRACT, file=sys.stderr)
    if FAILS:
        raise RuntimeError("authentication failed " + EXTRACT)
""".replace("EXTRACT", extract)
        .replace("VALUE", expression)
        .replace("FAILS", repr(fails))
    )
    case = run_allure_case(tmp_path, "call" if fails else "success", "pin", order, source=source)
    assert case["exit_code"] == (1 if fails else 0), case["terminal"]
    assert case["leaks"] == [], f"Canary leaked to {case['leaks']}"
    result = next(
        value for name, value in case["documents"].items() if name.endswith("-result.json")
    )
    assert result["name"] == "test_login[***]"
    assert result["status"] == ("broken" if fails else "passed")
    attachments = "\n".join(case["attachments"].values())
    for operation in (
        "setup stdout",
        "setup stderr",
        "Authenticating with",
        "call stderr",
        "teardown stdout",
        "teardown stderr",
    ):
        assert operation + " ***" in attachments


@pytest.mark.parametrize("custom_ids", [False, True], ids=["default-id", "custom-id"])
@pytest.mark.parametrize("numeric", [False, True], ids=["text", "numeric"])
@pytest.mark.parametrize("order", ["dolphin-first", "allure-first", "entry-point"])
def test_sensitive_parameter_value_is_removed_from_real_allure_test_name(
    tmp_path, custom_ids, numeric, order
):
    source = """
import os
import pytest

@pytest.mark.parametrize("pin", [VALUE], IDS)
def test_login(pin):
    assert pin
""".replace("IDS", 'ids=lambda value: "customer-" + str(value)' if custom_ids else "ids=None")
    source = source.replace(
        "VALUE", 'int(os.environ["KAN469_CANARY"])' if numeric else 'os.environ["KAN469_CANARY"]'
    )
    case = run_allure_case(
        tmp_path,
        "success",
        "pin",
        order,
        source=source,
        canary="739184527361" if numeric else None,
    )
    assert case["exit_code"] == 0, case["terminal"]
    assert case["leaks"] == []
    result = next(
        value for name, value in case["documents"].items() if name.endswith("-result.json")
    )
    assert result["name"] == ("test_login[customer-***]" if custom_ids else "test_login[***]")
    assert result["parameters"] == [{"name": "pin", "value": "***"}]
    assert result["status"] == "passed"
    assert len(result["uuid"]) == 36 and "***" not in result["uuid"]
    for key in ("historyId", "testCaseId"):
        assert len(result[key]) == 32 and "***" not in result[key]
    containers = [
        value for name, value in case["documents"].items() if name.endswith("-container.json")
    ]
    assert any(result["uuid"] in container.get("children", []) for container in containers)


@pytest.mark.parametrize("order", ["dolphin-first", "allure-first", "entry-point"])
def test_parameter_snapshot_survives_fixture_mutation(tmp_path, order):
    source = """
import os
import pytest

@pytest.fixture
def original(pin):
    value = pin.pop("code")
    print("fixture input", value)
    return value

@pytest.mark.parametrize("pin", [{"code": os.environ["KAN469_CANARY"]}], ids=lambda p: p["code"])
def test_login(pin, original):
    print("Authenticating with", original)
    assert pin == {}
"""
    case = run_allure_case(tmp_path, "success", "pin", order, source=source)
    assert case["exit_code"] == 0, case["terminal"]
    assert case["leaks"] == []
    result = next(
        value for name, value in case["documents"].items() if name.endswith("-result.json")
    )
    assert result["name"] == "test_login[***]"
    attachments = "\n".join(case["attachments"].values())
    assert "Authenticating with ***" in attachments and "fixture input ***" in attachments


@pytest.mark.parametrize("order", ["dolphin-first", "allure-first", "entry-point"])
def test_dynamic_sensitive_parameter_leaves_protect_real_captured_output(tmp_path, order):
    source = """
import os
import allure

def test_login():
    value = os.environ["KAN469_CANARY"]
    allure.dynamic.parameter("pin", {"code": value})
    allure.dynamic.parameter("pin", "***")
    allure.dynamic.title("login " + value)
    print("Authenticating with", value)
"""
    case = run_allure_case(tmp_path, "success", "pin", order, source=source)
    assert case["exit_code"] == 0, case["terminal"]
    assert case["leaks"] == []
    result = next(
        value for name, value in case["documents"].items() if name.endswith("-result.json")
    )
    assert result["name"] == "login ***"
    assert "Authenticating with ***" in "\n".join(case["attachments"].values())


def test_dynamic_parameter_context_is_test_scoped_and_survives_metadata_masking():
    from dolphin_desktop._redaction import _PARAMETER_VALUES

    redactor = _AllureRedactor()
    token = _PARAMETER_VALUES.set(None)
    try:
        redactor.add_parameter("pin", "not-active")
        assert _PARAMETER_VALUES.get() is None
        state = set()
        _PARAMETER_VALUES.set(state)
        redactor.add_parameter("public", "visible-operation")
        assert not state
        redactor.add_parameter("pin", {"code": "DYNAMIC_CANARY_739184"})
        redactor.add_parameter("pin", "***")
        result = AllureResult(
            name="login DYNAMIC_CANARY_739184", parameters=[Parameter(name="pin", value="***")]
        )
        _redact_item(result)
        assert result.name == "login ***"
        assert state == {"DYNAMIC_CANARY_739184"}
    finally:
        _PARAMETER_VALUES.reset(token)


@pytest.mark.parametrize(
    ("parameter", "display"),
    [
        ("739184", "739184"),
        (739184, "739184"),
        ("7", "7"),
        ("p\u00e4ss\u56db", r"p\xe4ss\u56db"),
        (b"739184", "739184"),
        (b"\xff", r"\xff"),
        ("quote'word", "quote'word"),
    ],
)
def test_sensitive_parameter_spellings_are_removed_without_changing_technical_ids(
    parameter, display
):
    result = AllureResult(
        uuid="739184-test-id",
        historyId="739184-history-id",
        testCaseId="739184-case-id",
        name=f"test_login[customer-{display}]",
        fullName=f"module#test_login[{display}]",
        parameters=[Parameter(name="pin", value=repr(parameter))],
        attachments=[Attachment(name=f"diagnostic {display}", source="739184.png")],
    )
    _redact_item(result)
    assert result.name == "test_login[customer-***]"
    assert result.fullName == "module#test_login[***]"
    assert result.parameters[0].value == "***"
    assert result.attachments[0].name == "diagnostic ***"
    assert result.uuid == "739184-test-id"
    assert result.historyId == "739184-history-id" and result.testCaseId == "739184-case-id"
    assert result.attachments[0].source == "739184.png"


def test_unparseable_parameter_repr_does_not_break_redaction():
    result = AllureResult(
        name="test_login['unterminated]",
        parameters=[Parameter(name="pin", value="'unterminated")],
    )
    _redact_item(result)
    assert result.name == "test_login[***]"
    assert result.parameters[0].value == "***"


def test_native_allure_attachment_requires_explicit_sanitization(tmp_path):
    source = """
import os
import allure
from pathlib import Path
from dolphin_desktop import Secret

def test_attachments():
    value = os.environ["KAN469_CANARY"]
    allure.attach("pin=" + value, name="native", attachment_type=allure.attachment_type.TEXT)
    allure.attach(
        f"pin={Secret(value)}", name="sanitized", attachment_type=allure.attachment_type.TEXT
    )
    path = Path("diagnostics.txt")
    path.write_text("pin=" + value, encoding="utf-8")
    allure.attach.file(str(path), name="native-file", attachment_type=allure.attachment_type.TEXT)
    path.write_text(f"pin={Secret(value)}", encoding="utf-8")
    allure.attach.file(
        str(path), name="sanitized-file", attachment_type=allure.attachment_type.TEXT
    )
"""
    case = run_allure_case(tmp_path, "success", "pin", "entry-point", source=source)
    assert case["exit_code"] == 0, case["terminal"]
    result = next(
        value for name, value in case["documents"].items() if name.endswith("-result.json")
    )
    sources = {attachment["name"]: attachment["source"] for attachment in result["attachments"]}
    # This is the explicitly documented native-SDK boundary, not a protected
    # Dolphin sink. Only the attachment whose caller formats Secret is safe.
    assert case["attachments"][sources["native"]] == "pin=" + case["canary"]
    assert case["attachments"][sources["sanitized"]] == "pin=***"
    assert case["attachments"][sources["native-file"]] == "pin=" + case["canary"]
    assert case["attachments"][sources["sanitized-file"]] == "pin=***"
    assert set(case["leaks"]) == {sources["native"], sources["native-file"]}


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
