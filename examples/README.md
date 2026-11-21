# Examples

These samples demonstrate the **0.2.0** API. Run them from this source
checkout. Windows and the prerequisites below are required for desktop tests.

## Setup and execution

From the repository root, install the locked development environment:

```powershell
uv sync --locked --group dev
uv run --no-sync pytest examples/notepad/ -v
```

The examples use their own `pytest.ini`, so the installed Dolphin plugin
provides the `desktop` and `launch` fixtures. The configuration also makes the
repository's local terminal server available to the mainframe demo.
Run examples individually; there is no single setup that provides every target
application. Missing optional prerequisites are reported as skips, not passes.
Use a dedicated test desktop/session for examples that use physical input.

| Example | Purpose | Prerequisites |
| --- | --- | --- |
| [notepad/](notepad/) | Native launch, text, selection and cleanup | Notepad; close existing Notepad windows first |
| [wpf/](wpf/) | AutomationId, lists and trees in the bundled WPF application | .NET 10 SDK; explicit build below |
| [webview2_sample/](webview2_sample/) | Mixed native WPF and HTML controls through UIA | .NET 10 SDK, WebView2 Runtime, explicit build below |
| [legacy_ie/](legacy_ie/) | Compatibility with an embedded MSHTML/Trident control | .NET SDK and .NET Framework 4.8 targeting support; explicit build below |
| [java_swing/](java_swing/) | JAB selectors and programmatic actions | Configured `JAVA_HOME`, compiled Swing demo, Java Access Bridge |
| [qt_demo/](qt_demo/) | Qt5/Qt6 applications used by the Qt test suites | The locked `qt-tests` group |
| [cdp/](cdp/) | DOM locators on a local page in isolated, headless Chromium | `cdp` extra and Playwright Chromium |
| [mainframe/](mainframe/) | Read a local TN3270 screen through the real emulator | `ws3270.exe` on PATH or `DOLPHIN_WS3270_PATH`; this source checkout |
| [sap_gui/](sap_gui/) | Read the status bar of an existing session | SAP GUI Scripting enabled; explicit opt-in below |
| [image_based/](image_based/) | Template matching and OCR | `vision` extra; a matching template or installed Tesseract |
| [office/](office/) | Excel/Word save and reopen with temporary valid documents | Installed desktop Excel/Word and pywin32; no user documents are opened |
| [object_repository.py](object_repository.py), [objects/](objects/) | Load YAML aliases without launching an application | Base package; UI snippets require your own matching application |

### WebView2 and legacy WPF hosts

```powershell
./examples/wpf/build.ps1
uv run --no-sync pytest examples/wpf/ -v

./examples/webview2_sample/build.ps1
uv run --no-sync pytest examples/webview2_sample/ -v

./examples/legacy_ie/build.ps1
uv run --no-sync pytest examples/legacy_ie/ -v
```

The legacy sample hosts `WebBrowser` inside a WPF process; it does not launch
the retired Internet Explorer browser. It exists for compatibility testing,
not as a browser choice for new applications. Tests do not build or restore
NuGet packages implicitly.

The standalone WPF app exposes three small windows used by its examples. Set
`DOLPHIN_WPF_APP` to your application's command and adapt the selectors to test
your own WPF target instead.

### Java Swing

Set `JAVA_HOME` to the absolute path of the trusted JDK/JRE that supplies JAB.
Compile with a matching JDK (or use `--release` for an older target runtime):

```powershell
& "$env:JAVA_HOME/bin/javac.exe" examples/java_swing/SwingDemo.java
& "$env:JAVA_HOME/bin/jabswitch.exe" /enable
uv run --no-sync pytest examples/java_swing/ -v
uv run --no-sync dolphin-run pytest examples/java_swing/test_java_swing_headless.py -v
```

The headless example uses programmatic JAB operations. Physical typing/clicking
in the other Swing example requires a visible desktop. Both launch `java.exe`
from `JAVA_HOME`, rather than a different Java version on PATH.

### Qt

```powershell
uv sync --locked --group dev --group qt-tests
uv run --no-sync python examples/qt_demo/all_widgets.py
uv run --no-sync python examples/qt_demo/all_widgets_qt5.py
```

The Qt applications are shared fixtures. Focused component regressions live in
[tests/qt/](../tests/qt/), while full application acceptance is maintained
separately. See the [Qt guide](../docs/guides/qt.md) for backend prerequisites.

### CDP and image/OCR

```powershell
uv sync --locked --group dev --extra cdp --extra vision
uv run --no-sync python -m playwright install chromium
uv run --no-sync pytest examples/cdp/ -v
uv run --no-sync pytest examples/image_based/ -v
```

The CDP sample opens only its local HTML file and uses a fresh browser profile.
The image example needs `DOLPHIN_BRUSH_TEMPLATE` pointing to your Paint brush
image, captured with `dolphin spy --image-pick`; no theme-dependent template is
bundled. OCR additionally needs the Tesseract executable on PATH. Image/OCR
samples require a visible desktop and matching application language/theme.

### External systems

For SAP, set `DOLPHIN_SAP_EXAMPLE=1` to inspect an existing test session. Optional
`DOLPHIN_SAP_CONNECTION` and `DOLPHIN_SAP_SESSION` select zero-based indices;
the example performs no transaction or database query.
This is an external integration example. The
[SAP framework tests](../tests/framework/test_sap.py) model the COM API and do
not replace a run against an authenticated SAP GUI session.

The TN3270 example starts a local server shared with the regression suite and
drives it through the real `ws3270` process. Download and extract the
[portable wc3270 build used by CI](https://x3270.bgp.nu/download/04.05/wc3270-4.5ga6-noinstall-64.zip)
and put `ws3270.exe` on PATH, or set
`DOLPHIN_WS3270_PATH` to its absolute path. Then run:

```powershell
uv run --no-sync pytest examples/mainframe/ -v
```

This demonstrates local protocol negotiation and screen reading without an
account or external system. It uses loopback plaintext; it does not verify TLS
or compatibility with an enterprise host. For remote connections and TLS
prerequisites, see the [mainframe guide](../docs/guides/mainframe.md).
The [TN3270 regression tests](../tests/mainframe/mock_tn3270/test_mock_roundtrip.py)
exercise the real emulator against the local server, including input and AID
keys. The [TLS transport tests](../tests/mainframe/mock_tn3270/test_tls_transport.py)
verify native TN5250 TLS and certificate rejection; they do not establish a
successful Windows TN3270 TLS connection to a real mainframe.

For additional stack-specific flows, see the [stack guides](../docs/guides/)
and [support matrix](../docs/support-matrix.md), including Delphi, Oracle Forms,
PowerBuilder, PowerPoint and classic Outlook. The
[Object Repository tutorial](../docs/tutorials/object-repository.md) describes
alias scoping and overrides.
