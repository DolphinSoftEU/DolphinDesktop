# Fallback selectors (self-healing)

UI elements sometimes change - a developer renames an `AutomationId`, or a new build ships
with a slightly different control hierarchy. Fallback selectors let Dolphin try alternative
criteria automatically before failing a test.

---

## The problem

A locator breaks when the primary selector stops matching:

```python
# Works today - AutomationId "btnSubmit"
btn = win.get_by_automation_id("btnSubmit")

# Next sprint: developer renamed it to "btnSave" - test fails
```

---

## Adding a fallback

Pass a `fallback` list when creating the locator.
Dolphin tries the primary criteria first; if that times out, it tries each fallback in order:

```python
btn = win.locator(
    auto_id="btnSubmit",
    fallback=[
        {"auto_id": "btnSave"},
        {"title": "Submit", "control_type": "Button"},
    ]
)
btn.click()
```

If the primary fails and `auto_id="btnSave"` matches, Dolphin clicks that element and logs
a self-healing event.

Fallbacks are explicit selector criteria. Dolphin does not infer semantic matches or replace
them with fuzzy/best-match lookup, so a similar title that does not match the declared
criteria still results in `ElementNotFoundError` and no self-healing event.

Each entry in `fallback` must be a selector mapping. Invalid entries are rejected when the
locator is created with a `ValueError` that identifies the zero-based entry, for example
`fallback[1]`; no lookup or self-healing event is attempted.

---

## Image-based fallback

When UIA criteria all fail, fall back to template matching:

```python
from dolphin_desktop import ImageLocator

btn = win.locator(
    auto_id="btnSubmit",
    fallback=[{"auto_id": "btnSave"}],
    image_fallback=ImageLocator("templates/submit_button.png"),
)
btn.click()
```

The template image is matched against the live screen using OpenCV.
Requires `pip install dolphin-desktop[vision]`.

If the template file is missing, `FileNotFoundError` is raised with the
`image_fallback` path. If the file exists but cannot be decoded, `ValueError` is raised.
A valid template that simply does not match the screen keeps the normal locator
not-found behavior.

---

## Inspecting self-healing events

```bash
dolphin selfheal-stats --last 20
```

```
Last 3 self-healing event(s):

  [2024-06-10 14:32:01]  tests/test_submit.py::test_submit_form
    primary:  {'auto_id': 'btnSubmit'}
    fallback: {'auto_id': 'btnSave'}
```

This shows which selectors degraded and helps you update them before they break completely.

---

## Best practices

1. **Prefer `auto_id` as primary** - AutomationId is set by developers and is the most stable identifier.
2. **Add a title-based fallback** - titles change with locale but rarely disappear.
3. **Use image fallback as last resort** - image matching is slower and more fragile than UIA.
4. **Review `selfheal-stats` after every release** - a self-healed test is a warning, not a pass.

---

## Using fallbacks in Page Objects

```python title="objects/form_page.py"
class FormPage:
    def __init__(self, window):
        self._win = window

    @property
    def _submit_button(self):
        return self._win.locator(
            auto_id="btnSubmit",
            fallback=[
                {"auto_id": "btnSave"},
                {"title": "Submit"},
            ],
        )

    def submit(self):
        self._submit_button.click()
```

---

## Next steps

- [Tracing and debugging](tracing.md) - record traces to diagnose selector failures
- [API Reference: Locator](../reference/locator.md) - `fallback` and `image_fallback` parameters
