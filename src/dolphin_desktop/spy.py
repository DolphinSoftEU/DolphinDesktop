"""Public ``dolphin_desktop.spy`` module — re-exports from ``_spy``.

Usage::

    import dolphin_desktop.spy as spy
    tree = spy.inspect(title="Notepad")
    print(spy.format_tree(tree["root"]))
    picked = spy.pick()                          # {"status": "ok", "chain": [...], ...}

    # SAP GUI for Windows (via SAP GUI Scripting):
    tree = spy.sap_inspect()
    print(spy.format_sap_tree(tree["root"]))
    picked = spy.sap_pick()                      # {"status": "ok", "selector": {...}, ...}

Every entry point returns a ``schema_version``-tagged dict, so a non-Python
front-end can tell a verified result from a degraded one without parsing stdout.
"""

from dolphin_desktop._spy import (
    SCHEMA_VERSION,
    format_sap_tree,
    format_tree,
    inspect,
    pick,
    sap_inspect,
    sap_pick,
)

__all__ = [
    "SCHEMA_VERSION",
    "format_sap_tree",
    "format_tree",
    "inspect",
    "pick",
    "sap_inspect",
    "sap_pick",
]
