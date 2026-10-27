"""Sanitize the completed JUnit artifact without changing pytest's node IDs."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from xml.etree import ElementTree

import pytest

from ._redaction import _redact_with_values


class _JUnitTreeBuilder(ElementTree.TreeBuilder):
    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        # pytest never emits a DTD. Reject it before any entity references can
        # be expanded, even if another process has replaced the XML file.
        raise ValueError("DTD is not permitted in the pytest JUnit artifact")


class _JUnitRedactor:
    def __init__(self) -> None:
        self.values: set[str] = set()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        # A serializable report attribute also reaches the xdist controller;
        # it cannot inspect the worker's Item or ContextVar.
        self.values.update(getattr(report, "_dolphin_parameter_values", ()))

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
        secrets = tuple(sorted(self.values, key=len, reverse=True))
        for element in tree.iter():
            # Preserve verdict counters, timings, line numbers and schema tags.
            for name in ("name", "classname", "message", "file", "url", "value"):
                if name in element.attrib:
                    element.attrib[name] = _redact_with_values(element.attrib[name], secrets)
            if element.text:
                element.text = _redact_with_values(element.text, secrets)
            if element.tail:
                element.tail = _redact_with_values(element.tail, secrets)
        tree.write(output, encoding="utf-8", xml_declaration=True)
