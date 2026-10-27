"""Sanitize the completed JUnit artifact without changing pytest's node IDs."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree

import pytest

from ._redaction import _redact_with_values

_TEST_TOKEN_PROPERTY = "__dolphin_junit_redaction_token"
_CONTROL_ATTRIBUTES = {
    "testsuite": frozenset({"tests", "failures", "errors", "skipped", "time", "timestamp"}),
    "testsuites": frozenset({"tests", "failures", "errors", "skipped", "time", "timestamp"}),
    "testcase": frozenset({"time", "line"}),
}


def _redact_xml(
    element: ElementTree.Element,
    values: dict[str, set[str]],
    secrets: tuple[str, ...] = (),
) -> None:
    """Keep parameter values inside their testcase; apply ordinary filters elsewhere."""
    parent_secrets = secrets
    if element.tag == "testcase":
        own_values: set[str] = set()
        for properties in element.findall("properties"):
            for prop in list(properties):
                token = prop.get("value", "")
                if prop.get("name") == _TEST_TOKEN_PROPERTY and token in values:
                    own_values.update(values[token])
                    properties.remove(prop)
            if not len(properties):
                element.remove(properties)
        secrets = tuple(sorted(own_values, key=len, reverse=True))
    protected = _CONTROL_ATTRIBUTES.get(element.tag, frozenset())
    for name, value in list(element.attrib.items()):
        if name not in protected:
            element.set(name, _redact_with_values(value, secrets))
    if element.text:
        element.text = _redact_with_values(element.text, secrets)
    for child in element:
        _redact_xml(child, values, secrets)
    if element.tail:
        element.tail = _redact_with_values(element.tail, parent_secrets)


class _JUnitTreeBuilder(ElementTree.TreeBuilder):
    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        # pytest never emits a DTD. Reject it before any entity references can
        # be expanded, even if another process has replaced the XML file.
        raise ValueError("DTD is not permitted in the pytest JUnit artifact")


class _JUnitRedactor:
    def __init__(self) -> None:
        self.values: dict[str, set[str]] = {}
        self.tokens: dict[tuple[str, str | None, int | None], str] = {}

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        # A serializable report attribute also reaches the xdist controller;
        # it cannot inspect the worker's Item or ContextVar.
        key = (
            report.nodeid,
            getattr(report, "worker_id", None),
            getattr(report, "item_index", None),
        )
        token = self.tokens.get(key)
        if token is None:
            token = getattr(report, "_dolphin_junit_token", None) or uuid.uuid4().hex
            self.tokens[key] = token
        report.__dict__["_dolphin_junit_token"] = token
        self.values.setdefault(token, set()).update(
            getattr(report, "_dolphin_parameter_values", ())
        )
        marker = (_TEST_TOKEN_PROPERTY, token)
        # Run before pytest's JUnit hook. An opaque property survives xdist,
        # duplicate/custom display names and split call/teardown failures;
        # the publication pass removes it from every emitted testcase.
        if not any(tuple(prop) == marker for prop in report.user_properties):
            report.user_properties.append(marker)

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_sessionfinish(self, session: pytest.Session) -> Iterator[None]:
        yield
        path = session.config.getoption("xmlpath", default=None)
        if not path or hasattr(session.config, "workerinput"):
            return
        output = Path(os.path.expandvars(path)).expanduser().resolve()
        # Both parser entry points use the DTD-rejecting target above. No DTD
        # (internal or external) can reach text/entity expansion; covered by
        # test_junit_publication_rejects_all_dtds.
        parser = ElementTree.XMLParser(target=_JUnitTreeBuilder())  # nosec B314
        tree = ElementTree.parse(output, parser=parser)  # nosec B314
        _redact_xml(tree.getroot(), self.values)
        tree.write(output, encoding="utf-8", xml_declaration=True)
