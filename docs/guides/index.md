# Guides

Use these guides after the quickstart when you know what kind of application you need to test.

| Guide | Start here when |
| --- | --- |
| [Native Windows Apps](native.md) | The app is Win32, WPF, WinForms, MFC, Delphi, or VB6 |
| [Electron](electron.md) | The app is built with Electron or Chromium UI |
| [WebView2](webview2.md) | The app embeds Microsoft Edge WebView2 |
| [CEF and Legacy IE](cef-legacy.md) | The app embeds CEF or old Trident/MSHTML controls |
| [Java Swing / AWT](java.md) | The app is a Java desktop application |
| [Office](office.md) | The test needs Excel or Word automation through COM |
| [Image-Based Fallback](image-based.md) | Accessibility APIs cannot see a rendered control |
| [Headless Mode](../tutorials/headless-mode.md) | You need to run tests on a hidden Windows desktop |
| [CI Setup](../ci/index.md) | You need artifact collection and Windows runner guidance |

Prefer UIA or Win32 selectors when they are available. Use image matching only for controls that are not exposed through accessibility APIs.
