# Examples

Working code samples for each supported app type.

| Example | App type | What it demonstrates |
|---------|----------|---------------------|
| [notepad/](notepad/) | Native Win32 | Launch, type, read, clear |
| [wpf/](wpf/) | WPF (.NET) | AutomationId, combo box, list |
| [webview2_sample/](webview2_sample/) | Edge WebView2 | launch_webview2, mixed native+web |
| [legacy_ie/](legacy_ie/) | Legacy IE / Trident | WPF WebBrowser automation |
| [java_swing/](java_swing/) | Java Swing | launch_java, JABLocator roles |
| [image_based/](image_based/) | Image fallback | ImageLocator, Screen OCR |
| [office/](office/) | Excel + Word | COM automation |

## Running examples

```bash
pip install dolphin-desktop pytest

# Run all examples (requires apps to be installed)
pytest examples/ -v -m integration

# Run a specific example
pytest examples/notepad/ -v
```
