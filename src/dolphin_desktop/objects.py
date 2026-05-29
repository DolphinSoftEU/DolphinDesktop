"""Object Repository — maps YAML aliases to pywinauto selectors."""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ._exceptions import AliasNotFoundError

# Map YAML-friendly key names → pywinauto criteria keys
_SELECTOR_KEY_MAP: dict[str, str] = {
    "automation_id": "auto_id",
    "role": "control_type",
    "name": "title",
}

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


def _map_selector(raw: dict[str, Any]) -> dict[str, Any]:
    return {_SELECTOR_KEY_MAP.get(k, k): v for k, v in raw.items()}


def _parse_entry(raw: dict[str, Any]) -> ObjectEntry:
    selector = _map_selector(raw["selector"])
    fallback = [_map_selector(fb) for fb in raw.get("fallback") or []]
    children = {k: _parse_entry(v) for k, v in (raw.get("children") or {}).items()}
    return ObjectEntry(selector=selector, fallback=fallback, children=children)


def _parse_entries(data: dict[str, Any]) -> dict[str, ObjectEntry]:
    return {alias: _parse_entry(entry) for alias, entry in data.items()}


def _validate_entry(entry: Any, path_label: str, source: Path) -> None:
    if not isinstance(entry, dict):
        raise ValueError(f"{source}: '{path_label}' must be a mapping, got {type(entry).__name__}")
    if "selector" not in entry:
        raise ValueError(f"{source}: '{path_label}' missing required 'selector' key")
    if not isinstance(entry["selector"], dict):
        raise ValueError(f"{source}: '{path_label}.selector' must be a mapping")
    fallback = entry.get("fallback")
    if fallback is not None:
        if not isinstance(fallback, list):
            raise ValueError(f"{source}: '{path_label}.fallback' must be a list")
        for i, fb in enumerate(fallback):
            if not isinstance(fb, dict):
                raise ValueError(f"{source}: '{path_label}.fallback[{i}]' must be a mapping")
    children = entry.get("children")
    if children is not None:
        if not isinstance(children, dict):
            raise ValueError(f"{source}: '{path_label}.children' must be a mapping")
        for child_alias, child_entry in children.items():
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

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self, path: str | Path, *, level: str = "project") -> None:
        """Load aliases from a YAML file at the given *level*.

        Args:
            path: Path to the YAML file.
            level: One of ``"workspace"``, ``"project"`` (default), or ``"test"``.
        """
        if level not in _LEVELS:
            raise ValueError(f"level must be one of {_LEVELS!r}, got {level!r}")

        import yaml  # lazy — only needed if objects feature is used

        path = Path(path)
        with path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

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

        self._registry[level].update(entries)
        self._files.append(_LoadedFile(path=path, level=level, mtime=path.stat().st_mtime))

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

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Registry management
    # ------------------------------------------------------------------

    def clear(self, level: str | None = None) -> None:
        """Clear the registry.

        Args:
            level: If given, clear only that level. Otherwise clear all levels.
        """
        if level is None:
            for lv in _LEVELS:
                self._registry[lv].clear()
            self._files.clear()
        else:
            self._registry[level].clear()
            self._files = [f for f in self._files if f.level != level]

    def available(self) -> list[str]:
        """Return a sorted list of all registered top-level alias names."""
        seen: set[str] = set()
        for level in _LEVELS:
            seen.update(self._registry[level])
        return sorted(seen)

    # ------------------------------------------------------------------
    # Dev-mode file watching
    # ------------------------------------------------------------------

    def enable_watch(self) -> None:
        """Enable reload-on-change: files are re-read when mtime changes."""
        self._watch_enabled = True

    def disable_watch(self) -> None:
        """Disable reload-on-change."""
        self._watch_enabled = False

    def _reload_changed(self) -> None:
        import yaml

        for lf in list(self._files):
            try:
                current_mtime = lf.path.stat().st_mtime
                if current_mtime > lf.mtime:
                    with lf.path.open(encoding="utf-8") as fh:
                        data = yaml.safe_load(fh) or {}
                    _validate(data, lf.path)
                    entries = _parse_entries(data)
                    self._registry[lf.level].update(entries)
                    lf.mtime = current_mtime
            except Exception:
                pass

    def __repr__(self) -> str:
        counts = {lv: len(self._registry[lv]) for lv in _LEVELS}
        return f"ObjectRepository({counts})"


# ---------------------------------------------------------------------------
# Module-level singleton and convenience API
# ---------------------------------------------------------------------------

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
    """Return all registered top-level alias names."""
    return _repository.available()


def enable_watch() -> None:
    """Enable reload-on-change for the global repository (dev mode)."""
    _repository.enable_watch()


def disable_watch() -> None:
    """Disable reload-on-change for the global repository."""
    _repository.disable_watch()
