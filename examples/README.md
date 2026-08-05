# Examples

Working code samples for each supported app type.

| Example | App type | What it demonstrates |
|---------|----------|---------------------|
| [notepad/](notepad/) | Native Win32 | Launch, type, read, clear |
| [wpf/](wpf/) | WPF (.NET) | AutomationId, combo box, list |
| [webview2_sample/](webview2_sample/) | Edge WebView2 | launch_webview2, mixed native+web |
| [legacy_ie/](legacy_ie/) | Legacy IE / Trident | WPF WebBrowser automation |
| [java_swing/](java_swing/) | Java Swing | launch_java, JABLocator roles |
| [qt_demo/](qt_demo/) | Qt 5 / Qt 6 | Demo apps driven by the Qt test suite (widgets, QML, charts) |
| [sap_gui/](sap_gui/) | SAP GUI | Attach to a running SAP GUI session via GUI Scripting |
| [image_based/](image_based/) | Image fallback | ImageLocator, Screen OCR |
| [office/](office/) | Excel + Word | COM automation |
| [object_repository.py](object_repository.py) + [objects/](objects/) | Any | YAML alias repository — see the [Object Repository tutorial](../docs/tutorials/object-repository.md) |

## Running examples

```bash
pip install dolphin-desktop pytest

# Run all examples (requires apps to be installed)
pytest examples/ -v -m integration

# Run a specific example
pytest examples/notepad/ -v
```
