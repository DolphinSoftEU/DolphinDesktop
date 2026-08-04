"""Every dolphin API the ``dolphin init`` scaffolds generate must actually exist.

A generated test that dies with ``AttributeError`` on its first run is worse than no
scaffold at all, and nothing else in the suite ever executes these templates. The
checker below walks each template's AST, follows return annotations from one call to
the next, and asserts every attribute it reaches is real — no application is launched.
"""

from __future__ import annotations

import ast
import inspect
import os
from typing import Any

import pytest

import dolphin_desktop
from dolphin_desktop import _cli, pytest_plugin
from dolphin_desktop._application import Application
from dolphin_desktop._cdp import CDPFrameLocator, CDPLocator, CDPSession
from dolphin_desktop._delphi import DelphiApp, DelphiComponent, DelphiForm
from dolphin_desktop._desktop import Desktop
from dolphin_desktop._locator import Locator
from dolphin_desktop._mainframe import MainframeTerminal
from dolphin_desktop._oracle_forms import OracleFormsApp, OracleFormsItem, OracleFormsWindow
from dolphin_desktop._sap import SapConnection, SapGui, SapSession
from dolphin_desktop._window import Window

_CLASSES: dict[str, type] = {
    cls.__name__: cls
    for cls in (
        Application,
        CDPFrameLocator,
        CDPLocator,
        CDPSession,
        DelphiApp,
        DelphiComponent,
        DelphiForm,
        Desktop,
        Locator,
        MainframeTerminal,
        OracleFormsApp,
        OracleFormsItem,
        OracleFormsWindow,
        SapConnection,
        SapGui,
        SapSession,
        Window,
    )
}

_TEMPLATES: dict[str, str] = {
    "conftest": _cli._T_CONFTEST,
    "minimal": _cli._T_TEST_MINIMAL,
    "standard": _cli._T_TEST_STANDARD,
    "notepad_page": _cli._T_NOTEPAD_PAGE,
    **{f"stack-{stack}": src for stack, src in _cli._STACK_TEMPLATES.items()},
}

_UNKNOWN = object()
_ARG = object()  # placeholder value for signature binding

# Entry points annotated ``-> Any`` that the scaffolds chain off; pinning the
# documented return type here keeps the rest of the chain checkable.
_RETURN_OVERRIDES: dict[Any, type] = {Window.get_by_role: Locator}


def _fixture_launch(cmd: str, **kwargs: Any) -> Application:
    """Mirrors the plugin's ``launch`` fixture, whose return type the AST cannot see."""
    raise NotImplementedError


class _ApiChecker(ast.NodeVisitor):
    """Resolves expression types through return annotations and checks every access."""

    def __init__(self) -> None:
        self.env: dict[str, Any] = {"launch": _fixture_launch}
        # Fixture name → the type it yields, so a test's parameters resolve too
        self.fixtures: dict[str, Any] = {}
        # self.<attr> assignments, which outlive the method scope that made them
        self.attrs: dict[str, Any] = {}
        self.errors: list[str] = []

    # Type resolution

    def _resolve(self, annotation: Any) -> Any:
        if not isinstance(annotation, str):
            return _UNKNOWN
        return _CLASSES.get(annotation.strip().strip("'\""), _UNKNOWN)

    def _return_annotation(self, func: Any) -> Any:
        try:
            return inspect.signature(func).return_annotation
        except (TypeError, ValueError):
            return inspect.Signature.empty

    def _type_of(self, node: ast.AST) -> Any:
        if isinstance(node, ast.Name):
            return self.env.get(node.id, _UNKNOWN)
        if isinstance(node, ast.Attribute):
            if _is_self(node):
                return self.attrs.get(node.attr, _UNKNOWN)
            owner = self._type_of(node.value)
            if owner is _UNKNOWN:
                return _UNKNOWN
            if not hasattr(owner, node.attr):
                self.errors.append(f"{_label(owner)}.{node.attr} does not exist")
                return _UNKNOWN
            return getattr(owner, node.attr)
        if isinstance(node, ast.Call):
            func = self._type_of(node.func)
            if func is _UNKNOWN:
                return _UNKNOWN
            self._check_signature(func, node)
            if inspect.isclass(func):
                return func
            if func in _RETURN_OVERRIDES:
                return _RETURN_OVERRIDES[func]
            return self._resolve(self._return_annotation(func))
        self.generic_visit(node)
        return _UNKNOWN

    def _check_signature(self, func: Any, node: ast.Call) -> None:
        if any(isinstance(a, ast.Starred) for a in node.args):
            return
        if any(kw.arg is None for kw in node.keywords):
            return
        try:
            sig = inspect.signature(func)
        except (TypeError, ValueError):
            return
        args = [_ARG] * len(node.args)
        params = list(sig.parameters)
        if inspect.isfunction(func) and params and params[0] == "self":
            args.insert(0, _ARG)
        try:
            sig.bind(*args, **{kw.arg: _ARG for kw in node.keywords})
        except TypeError as exc:
            self.errors.append(f"{_label(func)}{sig}: {exc}")

    # Bindings

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module != "dolphin_desktop":
            return
        for alias in node.names:
            if not hasattr(dolphin_desktop, alias.name):
                self.errors.append(f"dolphin_desktop.{alias.name} does not exist")
                continue
            self.env[alias.asname or alias.name] = getattr(dolphin_desktop, alias.name)

    def visit_Assign(self, node: ast.Assign) -> None:
        value = self._type_of(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.env[target.id] = value
            elif isinstance(target, ast.Attribute) and _is_self(target):
                self.attrs[target.attr] = value
            elif isinstance(target, ast.Tuple):
                for name, part in zip(target.elts, self._tuple_types(node.value), strict=False):
                    if isinstance(name, ast.Name):
                        self.env[name.id] = part

    def _tuple_types(self, node: ast.AST) -> list[Any]:
        """Split a ``tuple[A, B]`` return annotation into its element types."""
        if not isinstance(node, ast.Call):
            return []
        func = self._type_of(node.func)
        annotation = self._return_annotation(func) if func is not _UNKNOWN else None
        if not isinstance(annotation, str) or not annotation.startswith("tuple["):
            return []
        inner = annotation[len("tuple[") : annotation.rindex("]")]
        return [self._resolve(part) for part in inner.split(",")]

    def visit_With(self, node: ast.With) -> None:
        for item in node.items:
            entered = self._type_of(item.context_expr)
            if entered is not _UNKNOWN and item.optional_vars is not None:
                yielded = _UNKNOWN
                if hasattr(entered, "__enter__"):
                    yielded = self._resolve(self._return_annotation(entered.__enter__))
                else:
                    self.errors.append(f"{_label(entered)} is not a context manager")
                if isinstance(item.optional_vars, ast.Name):
                    self.env[item.optional_vars.id] = entered if yielded is _UNKNOWN else yielded
        for stmt in node.body:
            self.visit(stmt)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        outer = dict(self.env)
        if _is_fixture(node):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Yield | ast.Return) and sub.value is not None:
                    self.fixtures[node.name] = self._type_of(sub.value)
                    break
        for arg in node.args.args:
            if arg.arg == "self":
                continue
            if isinstance(arg.annotation, ast.Name):
                self.env[arg.arg] = _CLASSES.get(arg.annotation.id, _UNKNOWN)
            elif arg.arg in self.fixtures:
                self.env[arg.arg] = self.fixtures[arg.arg]
            elif arg.arg not in self.env:
                self.env[arg.arg] = _UNKNOWN
        self.generic_visit(node)
        self.env = outer

    def visit_Expr(self, node: ast.Expr) -> None:
        self._type_of(node.value)

    def visit_Assert(self, node: ast.Assert) -> None:
        self._type_of(node.test)

    def visit_Call(self, node: ast.Call) -> None:
        self._type_of(node)


def _is_self(node: ast.Attribute) -> bool:
    return isinstance(node.value, ast.Name) and node.value.id == "self"


def _is_fixture(node: ast.FunctionDef) -> bool:
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Attribute) and target.attr == "fixture":
            return True
    return False


def _label(obj: Any) -> str:
    return getattr(obj, "__qualname__", None) or getattr(obj, "__name__", None) or repr(obj)


def _check(source: str) -> list[str]:
    checker = _ApiChecker()
    checker.visit(ast.parse(source))
    return checker.errors


# Every scaffold must be valid Python and reference only APIs that exist


@pytest.mark.parametrize("name", sorted(_TEMPLATES))
def test_template_compiles(name):
    source = _TEMPLATES[name]
    compile(source, f"<{name}>", "exec")
    tree = ast.parse(source)
    defined = [n.name for n in tree.body if isinstance(n, ast.FunctionDef | ast.ClassDef)]
    if name == "conftest":
        # The desktop/launch fixtures come from the plugin; the scaffold only
        # has to leave a valid, pytest-importing module behind.
        assert [n for n in tree.body if isinstance(n, ast.Import | ast.ImportFrom)]
    elif name == "notepad_page":
        assert defined == ["NotepadPage"]
    else:
        # Every scaffold pytest is pointed at must yield something to collect.
        assert [n for n in defined if n.startswith("test_")]


@pytest.mark.parametrize("name", sorted(_TEMPLATES))
def test_template_references_only_real_apis(name):
    assert _check(_TEMPLATES[name]) == []


def test_the_launch_fixture_the_scaffolds_use_is_provided_by_the_plugin():
    """The checker assumes launch() hands back an Application."""
    assert hasattr(pytest_plugin, "launch")


def test_the_checker_reports_a_missing_attribute():
    """Guards the guard — a checker that silently resolves nothing proves nothing."""
    source = "from dolphin_desktop import Desktop\n\n\ndef t():\n    Desktop().no_such_method()\n"
    assert _check(source) == ["Desktop.no_such_method does not exist"]


def test_the_checker_follows_return_annotations():
    source = (
        "from dolphin_desktop import Desktop\n\n\n"
        "def t():\n    Desktop().sap(timeout=5).no_such_method()\n"
    )
    assert _check(source) == ["SapGui.no_such_method does not exist"]


def test_the_checker_reports_an_unknown_keyword_argument():
    source = "from dolphin_desktop import Desktop\n\n\ndef t():\n    Desktop().sap(nope=1)\n"
    assert "nope" in "".join(_check(source))


def test_the_checker_follows_the_cdp_half_of_the_electron_tuple():
    """An unannotated CDPSession leaves the whole electron scaffold unchecked."""
    source = (
        "from dolphin_desktop import Desktop\n\n\n"
        "def t():\n"
        "    app, cdp = Desktop().launch_electron_cdp('code.exe')\n"
        "    cdp.no_such_method()\n"
    )
    assert _check(source) == ["CDPSession.no_such_method does not exist"]


# Template details the AST checker cannot judge


def test_the_sap_scaffold_does_not_require_a_logged_on_user():
    """SAP GUI with a connection open but nobody logged on must skip, not fail."""
    tree = ast.parse(_TEMPLATES["stack-sap"])
    asserts = [ast.unparse(node.test) for node in ast.walk(tree) if isinstance(node, ast.Assert)]
    assert not any("user" in a for a in asserts)


def test_the_electron_scaffold_expands_the_temp_directory():
    """Desktop.launch spawns without a shell, so %TEMP% would reach VS Code literally."""
    tree = ast.parse(_TEMPLATES["stack-electron"])
    cmd = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.JoinedStr)
        and any(
            isinstance(part, ast.Constant) and "user-data-dir" in part.value for part in node.values
        )
    )
    expanded = eval(
        compile(ast.Expression(cmd), "<electron>", "eval"),
        {"os": os, "VSCODE": "code.exe"},
    )
    assert "%TEMP%" not in expanded
    assert os.environ["TEMP"] in expanded
