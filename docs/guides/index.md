# Guides

Use these guides after the quickstart when you know what kind of application you need to test.

| Guide | Start here when |
| --- | --- |
| [Native Windows Apps](native.md) | The app is Win32, WPF, WinForms, MFC, or VB6 |
| [Delphi / VCL and Lazarus](delphi.md) | The app is built with Delphi (RAD Studio) or Lazarus/LCL |
| [PowerBuilder](powerbuilder.md) | The app is a PowerBuilder client (Appeon or classic) |
| [Electron](electron.md) | The app is built with Electron or Chromium UI |
| [Embedded Web](embedded-web.md) | The app hosts web content (Electron, CEF, WebView2 overview) |
| [WebView2](webview2.md) | The app embeds Microsoft Edge WebView2 |
| [CEF and Legacy IE](cef-legacy.md) | The app embeds CEF or old Trident/MSHTML controls |
| [Java Swing / AWT](java.md) | The app is a Java desktop application |
| [Oracle Forms](oracle-forms.md) | The app is an Oracle Forms client (Java applet / Web Start) |
| [Qt 5 / Qt 6](qt.md) | The app uses Qt widgets (financial, trading, KDE, AMD/NVIDIA control panels) |
| [IBM Mainframe](mainframe.md) | The test drives a 3270 / 5250 terminal session or an HLLAPI emulator |
| [Office](office.md) | The test needs Excel or Word automation through COM |
| [SAP GUI for Windows](sap.md) | The test targets classic SAP GUI with SAP GUI Scripting enabled |
| [Image-Based Fallback](image-based.md) | Accessibility APIs cannot see a rendered control |
| [Headless Mode](../tutorials/headless-mode.md) | You need to run tests on a hidden Windows desktop |
| [CI Setup](../ci/index.md) | You need artifact collection and Windows runner guidance |

Prefer UIA or Win32 selectors when they are available. Use image matching only for controls that are not exposed through accessibility APIs.
