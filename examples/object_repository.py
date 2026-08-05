"""Object Repository demo.

Shows how to define UI element aliases in YAML and use them in tests
instead of embedding raw selectors directly in test code.

Run this example:

    python examples/object_repository.py

The YAML file (examples/objects/login.yaml) is loaded once; every test
refers to elements by alias, so UI changes require only a single YAML edit.
"""

from pathlib import Path

from dolphin_desktop import objects

# ---------------------------------------------------------------------------
# 1.  Load the Object Repository
# ---------------------------------------------------------------------------
# Auto-discover all *.yaml files in examples/objects/
objects_dir = Path(__file__).parent / "objects"
objects.discover(objects_dir)

# Or load a specific file explicitly:
# objects.load("examples/objects/login.yaml")

print("Registered aliases:", objects.available())

# ---------------------------------------------------------------------------
# 2.  Use aliases in tests
# ---------------------------------------------------------------------------
# Instead of:
#   win.get_by_automation_id("txtEmail").type_text("user@example.com")
#
# Write:
#   win.element("email_input").type_text("user@example.com")
#
# The selector lives in the YAML — not scattered across 50 test files.

# app.window("login_window") resolves the alias → {title: "Login - MyApp"}
# desktop = Desktop()
# app = desktop.launch("myapp.exe")
# win = app.window("login_window")          # alias → criteria from YAML
# win.element("email_input").type_text("user@example.com")
# win.element("password_input").type_text("secret")
# win.element("submit_button").click()

# ---------------------------------------------------------------------------
# 3.  Override hierarchy demo
# ---------------------------------------------------------------------------
# Load workspace defaults first, project-specific overrides on top:
#
# objects.load("workspace/objects.yaml",    level="workspace")
# objects.load("project/objects.yaml",      level="project")
# objects.load("tests/test_login/objs.yaml", level="test")   # highest priority

# ---------------------------------------------------------------------------
# 4.  Dev-mode: reload YAML without restarting the test session
# ---------------------------------------------------------------------------
# objects.enable_watch()    # files are reloaded automatically when mtime changes
