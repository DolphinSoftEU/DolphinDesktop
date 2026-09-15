# Mainframe (3270 / 5250 / HLLAPI)

Mainframe terminal automation surface. See
[Mainframe guide](../guides/mainframe.md) for install steps and backend
selection.

`Desktop.mainframe()` accepts `tls=False` (the default), `tls_ca_file=`,
`server_hostname=`, and `insecure_tls=`. Port 992 does not implicitly enable
TLS. Plaintext on the normal port 23 remains the compatible default; plaintext
on port 992 requires the explicit `insecure_tls=True` opt-in. Native `tn5250`
TLS validates the CA and hostname before TN5250 data is sent. `s3270` uses its
`L:` transport prefix with `-verifycert`; `tls_ca_file` maps to `-cafile`, and
`insecure_tls=True` explicitly selects the unverified `-noverifycert` mode.

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
