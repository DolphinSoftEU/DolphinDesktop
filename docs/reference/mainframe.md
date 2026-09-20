# Mainframe (3270 / 5250 / HLLAPI)

Mainframe terminal automation surface. See
[Mainframe guide](../guides/mainframe.md) for install steps and backend
selection.

`Desktop.mainframe()` accepts `tls=False` (the default), `tls_cafile=`,
`tls_context=`, and `allow_plaintext=`. Port 992 does not implicitly enable
TLS. dolphin refuses a plaintext session to a remote host unless `tls=True`,
the host is loopback, or `allow_plaintext=True` is passed explicitly — a port
number, 992 included, is never treated as a substitute. Native `tn5250` TLS
validates the CA and hostname before TN5250 data is sent. `s3270` uses its
`L:` transport prefix and accepts `tls_cafile=`, but certificate verification
itself happens inside the emulator process, not in Python — see the
[Mainframe guide](../guides/mainframe.md#transport-security) for that trust
boundary.

For the line-oriented `s3270` backend, `connect()` and `type_text()` reject
control characters before process startup or stdin writes. Host action
delimiters are rejected too. Printable text is escaped as one literal
`String(...)` action; `MainframeError` identifies invalid host, port, or text
input.

::: dolphin_desktop._mainframe.MainframeTerminal
    options:
      show_root_heading: true
      show_source: false
      members_order: source
      filters: ["!^_"]

::: dolphin_desktop._mainframe.TerminalScreen
    options:
      show_root_heading: true
      show_source: false
      members_order: source
      filters: ["!^_"]

::: dolphin_desktop._mainframe.TerminalField
    options:
      show_root_heading: true
      show_source: false
      members_order: source
      filters: ["!^_"]

::: dolphin_desktop._mainframe.FieldInfo
    options:
      show_root_heading: true
      show_source: false
      members_order: source
      filters: ["!^_"]

::: dolphin_desktop._mainframe.AID
    options:
      show_root_heading: true
      show_source: false
      members_order: source

::: dolphin_desktop._mainframe.MainframeError
