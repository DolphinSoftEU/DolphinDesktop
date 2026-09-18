"""Object Repository — maps YAML aliases to pywinauto selectors."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._exceptions import AliasNotFoundError
from ._logging import get_logger

_log = get_logger("objects")

# Map YAML-friendly key names → pywinauto criteria keys
_SELECTOR_KEY_MAP: dict[str, str] = {
    "automation_id": "auto_id",
    "role": "control_type",
    "name": "title",
}

# Criteria pywinauto's find_elements accepts, plus the YAML-friendly aliases above.
# A key outside this set is a typo (``automationid:``) that would otherwise travel
# all the way into pywinauto and fail there, far from the file that caused it.
# ``parent`` is deliberately absent: ``Window.element()`` passes the window itself as
# ``Locator``'s first positional argument, so a selector carrying it raises TypeError.
_VALID_SELECTOR_KEYS = frozenset(_SELECTOR_KEY_MAP) | {
    "active_only",
    "auto_id",
    "backend",
    "best_match",
    "class_name",
    "class_name_re",
    "control_id",
    "control_type",
    "ctrl_index",
    "depth",
    "enabled_only",
    "found_index",
    "framework_id",
    "handle",
    "predicate_func",
    "process",
    "title",
    "title_re",
    "top_level_only",
    "visible_only",
}

_VALID_ENTRY_KEYS = frozenset({"selector", "fallback", "children"})

_LEVELS = ("workspace", "project", "test")


@dataclass
class ObjectEntry:
    """Resolved alias entry: selector criteria + optional fallbacks + children."""

    selector: dict[str, Any]
    fallback: list[dict[str, Any]] = field(default_factory=list)
    children: dict[str, ObjectEntry] = field(default_factory=dict)


@dataclass
class _LoadedFile:
    path: Path
    level: str
    mtime: float
    # resolve() polls every watched file, so an unreadable one is reported once
    # rather than on every element lookup
    unreadable_logged: bool = False
    # same, for a file whose stat() works but whose open() does not — a Windows
    # sharing violation on the file that was just saved
    unopenable_logged: bool = False


def _map_selector(raw: dict[str, Any]) -> dict[str, Any]:
    return {_SELECTOR_KEY_MAP.get(k, k): v for k, v in raw.items()}


def _parse_entry(raw: dict[str, Any]) -> ObjectEntry:
    selector = _map_selector(raw["selector"])
    fallback = [_map_selector(fb) for fb in raw.get("fallback") or []]
    children = {k: _parse_entry(v) for k, v in (raw.get("children") or {}).items()}
    return ObjectEntry(selector=selector, fallback=fallback, children=children)


def _parse_entries(data: dict[str, Any]) -> dict[str, ObjectEntry]:
    return {alias: _parse_entry(entry) for alias, entry in data.items()}


class _DuplicateYamlKeyError(Exception):
    def __init__(self, key: Any, yaml_path: str, mark: Any) -> None:
        super().__init__(f"duplicate key {key!r} at YAML path '{yaml_path}'")
        self.key = key
        self.yaml_path = yaml_path
        self.mark = mark


def _yaml_key(node: Any) -> Any:
    """Return a readable key for a composed YAML node."""
    if getattr(node, "value", None) is not None and not isinstance(node.value, list):
        return node.value
    return repr(getattr(node, "value", node))


def _yaml_key_signature(node: Any) -> tuple[Any, Any]:
    """Identify textual YAML keys without constructing arbitrary Python objects."""
    value = getattr(node, "value", None)
    try:
        hash(value)
    except TypeError:
        value = repr(value)
    return (getattr(node, "tag", None), value)


def _yaml_child_path(parent: str, key: Any) -> str:
    key_text = str(key)
    if parent == "$":
        return key_text
    if key_text == "selector":
        return f"{parent}.selector"
    if key_text == "fallback":
        return f"{parent}.fallback"
    if key_text == "children":
        return f"{parent}.children"
    if parent.endswith(".children"):
        return f"{parent[: -len('.children')]}.{key_text}"
    return f"{parent}.{key_text}"


def _check_unique_yaml_keys(node: Any, yaml_path: str = "$") -> None:
    """Reject duplicate keys in every mapping before PyYAML constructs it."""
    node_id = getattr(node, "id", None)
    if node_id == "mapping":
        seen: set[tuple[Any, Any]] = set()
        for key_node, value_node in node.value:
            signature = _yaml_key_signature(key_node)
            if signature in seen:
                raise _DuplicateYamlKeyError(_yaml_key(key_node), yaml_path, key_node.start_mark)
            seen.add(signature)
            _check_unique_yaml_keys(value_node, _yaml_child_path(yaml_path, _yaml_key(key_node)))
    elif node_id == "sequence":
        for index, item in enumerate(node.value):
            _check_unique_yaml_keys(item, f"{yaml_path}[{index}]")


def _yaml_location(mark: Any) -> str:
    if mark is None:
        return ""
    return f"line {mark.line + 1}, column {mark.column + 1}"


def _load_yaml(path: Path) -> Any:
    """Parse one repository file with duplicate-key and syntax diagnostics."""
    import yaml  # lazy — only needed if objects feature is used

    with path.open(encoding="utf-8") as fh:
        content = fh.read()

    try:
        node = yaml.compose(content, Loader=yaml.SafeLoader)
        if node is not None:
            _check_unique_yaml_keys(node)
        return yaml.safe_load(content)
    except _DuplicateYamlKeyError as exc:
        location = _yaml_location(exc.mark)
        location_suffix = f" ({location})" if location else ""
        raise ValueError(
            f"{path}: duplicate YAML key {exc.key!r} at '{exc.yaml_path}'{location_suffix}"
        ) from exc
    except yaml.YAMLError as exc:
        location = _yaml_location(getattr(exc, "problem_mark", None))
        location_suffix = f" at {location}" if location else ""
        detail = getattr(exc, "problem", None) or str(exc)
        raise ValueError(f"{path}: malformed YAML{location_suffix}: {detail}") from exc


def _validate_selector_keys(selector: dict[str, Any], path_label: str, source: Path) -> None:
    unknown = sorted(k for k in selector if k not in _VALID_SELECTOR_KEYS)
    if unknown:
        raise ValueError(
            f"{source}: '{path_label}' has unknown selector key(s) {unknown}. "
            f"Valid keys: {sorted(_VALID_SELECTOR_KEYS)}"
        )


def _validate_entry(entry: Any, path_label: str, source: Path) -> None:
    if not isinstance(entry, dict):
        raise ValueError(f"{source}: '{path_label}' must be a mapping, got {type(entry).__name__}")
    unknown_fields = [key for key in entry if key not in _VALID_ENTRY_KEYS]
    if unknown_fields:
        formatted = sorted(repr(key) for key in unknown_fields)
        raise ValueError(
            f"{source}: '{path_label}' has unknown alias field(s) {formatted}. "
            f"Valid fields: {sorted(_VALID_ENTRY_KEYS)}"
        )
    if "selector" not in entry:
        raise ValueError(f"{source}: '{path_label}' missing required 'selector' key")
    if not isinstance(entry["selector"], dict):
        raise ValueError(f"{source}: '{path_label}.selector' must be a mapping")
    _validate_selector_keys(entry["selector"], f"{path_label}.selector", source)
    fallback = entry.get("fallback")
    if fallback is not None:
        if not isinstance(fallback, list):
            raise ValueError(f"{source}: '{path_label}.fallback' must be a list")
        for i, fb in enumerate(fallback):
            if not isinstance(fb, dict):
                raise ValueError(f"{source}: '{path_label}.fallback[{i}]' must be a mapping")
            _validate_selector_keys(fb, f"{path_label}.fallback[{i}]", source)
    children = entry.get("children")
    if children is not None:
        if not isinstance(children, dict):
            raise ValueError(f"{source}: '{path_label}.children' must be a mapping")
        for child_alias, child_entry in children.items():
            if not isinstance(child_alias, str):
                raise ValueError(f"{source}: alias key must be a string, got {child_alias!r}")
            _validate_entry(child_entry, f"{path_label}.{child_alias}", source)


def _validate(data: Any, source: Path) -> None:
    if not isinstance(data, dict):
        raise ValueError(f"{source}: top-level must be a mapping, got {type(data).__name__}")
    for alias, entry in data.items():
        if not isinstance(alias, str):
            raise ValueError(f"{source}: alias key must be a string, got {alias!r}")
        _validate_entry(entry, alias, source)


class ObjectRepository:
    """Stores and resolves YAML-defined UI element aliases.

    Supports three override levels (lowest → highest priority):
    ``workspace`` < ``project`` < ``test``.

    Usage::

        repo = ObjectRepository()
        repo.load("objects/login.yaml")
        entry = repo.resolve("submit_button")
    """

    def __init__(self) -> None:
        self._registry: dict[str, dict[str, ObjectEntry]] = {lv: {} for lv in _LEVELS}
        self._files: list[_LoadedFile] = []
        self._watch_enabled: bool = False

    # Loading

    def load(self, path: str | Path, *, level: str = "project") -> None:
        """Load aliases from a YAML file at the given *level*.

        Args:
            path: Path to the YAML file.
            level: One of ``"workspace"``, ``"project"`` (default), or ``"test"``.
        """
        if level not in _LEVELS:
            raise ValueError(f"level must be one of {_LEVELS!r}, got {level!r}")

        path = Path(path).resolve()
        data = _load_yaml(path)

        if data is None:
            data = {}

        _validate(data, path)
        entries = _parse_entries(data)

        # Warn on duplicate aliases within the same level (cross-file)
        dupes = set(entries) & set(self._registry[level])
        if dupes:
            warnings.warn(
                f"{path}: aliases already registered at level={level!r} will be "
                f"overridden: {sorted(dupes)}",
                stacklevel=3,
            )

        mtime = path.stat().st_mtime
        self._registry[level].update(entries)
        self._files.append(_LoadedFile(path=path, level=level, mtime=mtime))

    def discover(self, directory: str | Path = "objects", *, level: str = "project") -> int:
        """Load all ``*.yaml`` / ``*.yml`` files from *directory*.

        Returns the number of files loaded.
        """
        directory = Path(directory)
        count = 0
        if directory.is_dir():
            for p in sorted(directory.glob("*.yaml")):
                self.load(p, level=level)
                count += 1
            for p in sorted(directory.glob("*.yml")):
                self.load(p, level=level)
                count += 1
        return count

    # Resolution

    def resolve(self, alias: str) -> ObjectEntry:
        """Return the :class:`ObjectEntry` for *alias* (highest-priority level wins).

        Raises:
            AliasNotFoundError: if *alias* is not registered.
        """
        if self._watch_enabled:
            self._reload_changed()

        for level in reversed(_LEVELS):
            if alias in self._registry[level]:
                return self._registry[level][alias]

        available = self.available()
        raise AliasNotFoundError(
            f"{alias!r} not found in object repository. Available: {available}"
        )

    def resolve_child(self, parent_alias: str, child_alias: str) -> ObjectEntry:
        """Return the :class:`ObjectEntry` for *child_alias* within *parent_alias*.

        Falls back to a flat top-level lookup if the child is not defined
        under the parent.

        Raises:
            AliasNotFoundError: if neither lookup succeeds.
        """
        parent = self.resolve(parent_alias)

        if child_alias in parent.children:
            return parent.children[child_alias]

        # Flat fallback: maybe the alias is registered at the top level
        try:
            return self.resolve(child_alias)
        except AliasNotFoundError:
            pass

        available_children = sorted(parent.children)
        available_all = self.available()
        raise AliasNotFoundError(
            f"{child_alias!r} not found as child of {parent_alias!r}. "
            f"Children of {parent_alias!r}: {available_children}. "
            f"All registered aliases: {available_all}"
        )

    # Registry management

    def clear(self, level: str | None = None) -> None:
        """Clear the registry.

        Args:
            level: If given, clear only that level. Otherwise clear all levels.

        Raises:
            ValueError: if *level* is not one of the known levels.
        """
        if level is None:
            for lv in _LEVELS:
                self._registry[lv].clear()
            self._files.clear()
            return
        if level not in _LEVELS:
            raise ValueError(f"level must be one of {_LEVELS!r}, got {level!r}")
        self._registry[level].clear()
        self._files = [f for f in self._files if f.level != level]

    def available(self) -> list[str]:
        """Return a sorted list of all registered top-level alias names."""
        seen: set[str] = set()
        for level in _LEVELS:
            seen.update(self._registry[level])
        return sorted(seen)

    # Dev-mode file watching

    def enable_watch(self) -> None:
        """Enable reload-on-change: files are re-read when mtime changes."""
        self._watch_enabled = True

    def disable_watch(self) -> None:
        self._watch_enabled = False

    def _mtime_advanced(self, lf: _LoadedFile) -> bool:
        try:
            advanced = lf.path.stat().st_mtime > lf.mtime
        except OSError as exc:
            if not lf.unreadable_logged:
                lf.unreadable_logged = True
                _log.warning("object repository file %s is unreadable: %s", lf.path, exc)
            return False
        lf.unreadable_logged = False
        return advanced

    def _reload_changed(self) -> None:
        """Re-read watched files whose mtime advanced.

        A changed file's whole level is rebuilt from its files rather than merged
        into the existing registry: ``update()`` alone can never drop an alias the
        user deleted from the YAML, so resolution would keep serving it. A file that
        fails to parse or validate leaves the previous definitions in place and its
        mtime unbumped, so the failure is re-reported until the file is fixed.

        A file that cannot be opened is left out of the rebuild instead of failing it,
        so one unreadable file cannot wedge the whole level at its current definitions.
        Its aliases stop resolving until it can be read again — deliberately: the level
        is rebuilt rather than merged precisely so a definition the user removed cannot
        keep being served, and nothing here can tell a genuine deletion from a passing
        sharing violation. The file itself stays watched and keeps its old mtime, which
        is what lets it come back; dropping the *file* would make its aliases
        unrecoverable for the rest of the run.
        """
        changed_levels = {lf.level for lf in self._files if self._mtime_advanced(lf)}
        for level in changed_levels:
            level_files = [lf for lf in self._files if lf.level == level]
            rebuilt: dict[str, ObjectEntry] = {}
            skipped: list[int] = []
            try:
                for lf in level_files:
                    try:
                        data = _load_yaml(lf.path)
                        if data is None:
                            data = {}
                    except OSError as exc:
                        if not lf.unopenable_logged:
                            lf.unopenable_logged = True
                            _log.warning(
                                "object repository file %s cannot be read, leaving its "
                                "aliases out of level=%r until it can: %s",
                                lf.path,
                                level,
                                exc,
                            )
                        skipped.append(id(lf))
                        continue
                    lf.unopenable_logged = False
                    _validate(data, lf.path)
                    rebuilt.update(_parse_entries(data))
            except Exception as exc:
                _log.warning(
                    "object repository reload of level=%r failed, keeping the previous "
                    "definitions: %s",
                    level,
                    exc,
                )
                continue
            self._registry[level] = rebuilt
            for lf in level_files:
                if id(lf) in skipped:
                    # Its mtime bump is still owed: bumping it here would make the
                    # rebuild that could bring the file back never run.
                    continue
                try:
                    lf.mtime = lf.path.stat().st_mtime
                except OSError:
                    pass

    def __repr__(self) -> str:
        counts = {lv: len(self._registry[lv]) for lv in _LEVELS}
        return f"ObjectRepository({counts})"


# Module-level singleton and convenience API

_repository = ObjectRepository()


def load(path: str | Path, *, level: str = "project") -> None:
    """Load aliases from a YAML file into the global Object Repository.

    Args:
        path: Path to the YAML file.
        level: Override level — ``"workspace"`` < ``"project"`` < ``"test"``.
    """
    _repository.load(path, level=level)


def discover(directory: str | Path = "objects", *, level: str = "project") -> int:
    """Auto-discover and load all ``*.yaml`` / ``*.yml`` files in *directory*.

    Returns the number of files loaded.
    """
    return _repository.discover(directory, level=level)


def clear(level: str | None = None) -> None:
    """Clear the global Object Repository (or a single level if *level* is given)."""
    _repository.clear(level)


def available() -> list[str]:
    return _repository.available()


def enable_watch() -> None:
    """Enable reload-on-change for the global repository (dev mode)."""
    _repository.enable_watch()


def disable_watch() -> None:
    """Disable reload-on-change for the global repository."""
    _repository.disable_watch()
