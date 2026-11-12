"""Mask pytest display identifiers without changing execution/report identities."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from os.path import relpath
from typing import Any

import pytest

from ._logging import _redact
from ._redaction import _redact_identifier


def _display_identifiers(nodeid: str, config: pytest.Config) -> tuple[str, str, str]:
    """Use the same root-relative, invocation-relative and name-only display forms."""
    # Config.cwd_relative_nodeid is internal: pytest 9.2 changed its input
    # from str to NodeId. Keep string IDs at our publication boundary and
    # derive the display path using public configuration attributes instead.
    path, separator, names = nodeid.partition("::")
    relative_nodeid = nodeid
    if config.invocation_params.dir != config.rootpath:
        fullpath = config.rootpath / path
        try:
            relative_path = relpath(fullpath, config.invocation_params.dir)
        except ValueError:  # Windows paths on different drives have no relative path.
            relative_path = str(fullpath)
        relative_nodeid = relative_path + separator + names
    # A parameter ID may itself contain '::'. Only split the unparametrized
    # hierarchy, then reattach the complete parameter suffix for collection's
    # name-only display. Exclude the file path, which may contain '[' too.
    name, bracket, parameters = (names if separator else path).partition("[")
    display_name = name.rsplit("::", 1)[-1] + bracket + parameters
    return nodeid, relative_nodeid, display_name


class _TerminalRedactor:
    def __init__(self, parameter_values: Callable[[pytest.Item], set[str]]) -> None:
        self.parameter_values = parameter_values
        self.identifiers: dict[str, str] = {}
        self._deferred_nodeid: str | None = None

    def _remember(self, nodeid: str, values: set[str], config: pytest.Config) -> None:
        for identifier in _display_identifiers(nodeid, config):
            safe = _redact_identifier(identifier, values)
            if safe != identifier:
                self.identifiers[identifier] = safe

    def redact(self, text: str) -> str:
        text = _redact(text)
        # Replace complete identifiers, not the session's union of secret
        # values: a PIN 'login' must not change an unrelated 'test_login'.
        for original in sorted(self.identifiers, key=len, reverse=True):
            text = text.replace(original, self.identifiers[original])
        return text

    @pytest.hookimpl(tryfirst=True)
    def pytest_sessionstart(self, session: pytest.Session) -> None:
        reporter = session.config.pluginmanager.getplugin("terminalreporter")
        if reporter is None:
            return
        writer = reporter._tw
        original = writer.write

        def write(text: str, **kwargs: Any) -> None:
            if self._deferred_nodeid is not None and any(
                identifier in text
                for identifier in _display_identifiers(self._deferred_nodeid, self.config)
            ):
                return
            original(self.redact(text), **kwargs)

        writer.write = write
        session.config.add_cleanup(lambda: setattr(writer, "write", original))

    @pytest.hookimpl(hookwrapper=True, tryfirst=True)
    def pytest_runtest_logstart(self, nodeid: str) -> Iterator[None]:
        previous = self._deferred_nodeid
        # xdist sends start events before the report's sensitive-value
        # snapshot. Defer the terminal's start line; the completed status line
        # is rendered after our logreport hook has learned the safe ID.
        if self.config.getoption("numprocesses", default=None):
            self._deferred_nodeid = nodeid
        try:
            yield
        finally:
            self._deferred_nodeid = previous

    @pytest.hookimpl(tryfirst=True)
    def pytest_collection_modifyitems(
        self, config: pytest.Config, items: list[pytest.Item]
    ) -> None:
        for item in items:
            self._remember(item.nodeid, self.parameter_values(item), config)

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        # xdist controllers do not collect Items. Worker snapshots reach them
        # on TestReport before the terminal reporter formats verbose output.
        self._remember(
            report.nodeid, set(getattr(report, "_dolphin_parameter_values", ())), self.config
        )

    def pytest_configure(self, config: pytest.Config) -> None:
        self.config = config
