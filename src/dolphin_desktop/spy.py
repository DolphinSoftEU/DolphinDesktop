"""Public ``dolphin_desktop.spy`` module — re-exports from ``_spy``.

Usage::

    import dolphin_desktop.spy as spy
    tree = spy.inspect(title="Notepad")
    print(spy.format_tree(tree["root"]))
    sel = spy.pick()
"""

from dolphin_desktop._spy import (
    SCHEMA_VERSION,
    format_tree,
    inspect,
    pick,
)

__all__ = ["SCHEMA_VERSION", "format_tree", "inspect", "pick"]
