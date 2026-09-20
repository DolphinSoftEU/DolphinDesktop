"""Security regression tests for the mainframe backends.

Covers three findings:

* KAN-467 — s3270 command injection: one public call must produce exactly one
  protocol action, and CR/LF/NUL in user data must not create a second one.
* KAN-468 — transport confidentiality: TN5250 must offer verified TLS with no
  silent fallback to plaintext, and a port number is never a substitute.
* KAN-469 — secret hygiene: typed text and raw frames must not reach the logs
  or the trace, and a :class:`Secret` is masked everywhere.

Everything here is headless: the s3270 subprocess is a fake, and the one TLS
test binds a throwaway server on 127.0.0.1 with the checked-in certificate.
"""

from __future__ import annotations

import socket
import ssl
import threading
from pathlib import Path

import pytest

import dolphin_desktop._mainframe as mf
from dolphin_desktop import Secret
from dolphin_desktop._logging import _redact

_TLS_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "tls"
_CERT = _TLS_DIR / "localhost.crt"
_KEY = _TLS_DIR / "localhost.key"


# --------------------------------------------------------------------------- #
# KAN-467 — s3270 command injection                                            #
# --------------------------------------------------------------------------- #


class _CapturingS3270(mf._S3270Backend):
    """s3270 backend that records the exact bytes written to the emulator."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self._trace = False

    def _exec(self, command, *, raise_on_error=True):  # type: ignore[override]
        # Exercise the real frame-delimiter guard before recording.
        mf._reject_frame_delimiters(command, "s3270 command")
        self.sent.append(command)
        return ([], "")


@pytest.mark.parametrize("payload", ["hello\nQuit()", "a\rb", "x\x00y", "line1\nline2"])
def test_send_string_rejects_frame_delimiters(payload: str) -> None:
    backend = _CapturingS3270()
    # send_string() layers two guards: a printable-only check (which names
    # the offending control character) runs before _s3270_quote's own
    # carriage-return/line-feed/NUL check — either wording proves the same
    # security property, that the payload never reaches the emulator.
    with pytest.raises(mf.MainframeError, match=r"carriage return|line feed|NUL|control character"):
        backend.send_string(payload)
    assert backend.sent == []  # nothing reached the emulator


def test_send_string_emits_exactly_one_action() -> None:
    backend = _CapturingS3270()
    backend.send_string('pa"ss\\word')
    assert len(backend.sent) == 1
    # The one action is a String(...) with the quote and backslash escaped.
    assert backend.sent[0] == r'String("pa\"ss\\word")'


def test_quote_escapes_but_never_adds_a_line() -> None:
    quoted = mf._s3270_quote('a"b\\c')
    assert "\n" not in quoted and "\r" not in quoted
    assert quoted == r'"a\"b\\c"'


@pytest.mark.parametrize(
    "host",
    ["host\nQuit()", "host\r\nString(x)", "Y:evilhost", "h ost", "host;rm", ""],
)
def test_host_validation_rejects_anything_but_a_name_or_ip(host: str) -> None:
    with pytest.raises(mf.MainframeError):
        mf._validate_host(host)


@pytest.mark.parametrize("host", ["pub400.com", "127.0.0.1", "MAINFRAME01", "[::1]", "a.b-c.d"])
def test_host_validation_accepts_plain_hosts(host: str) -> None:
    assert mf._validate_host(host) == host


def test_l_prefix_maps_to_tls_and_strips_the_prefix() -> None:
    tls, bare = mf._split_s3270_host("L:securehost")
    assert tls is True and bare == "securehost"
    tls, bare = mf._split_s3270_host("plainhost")
    assert tls is False and bare == "plainhost"


@pytest.mark.parametrize("port", [0, -1, 65536, 99999, True, "23"])
def test_port_validation_rejects_out_of_range(port) -> None:
    with pytest.raises(mf.MainframeError):
        mf._validate_port(port)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# KAN-468 — transport confidentiality                                          #
# --------------------------------------------------------------------------- #


def test_plaintext_to_a_remote_host_is_refused() -> None:
    backend = mf._Tn5250Backend()
    with pytest.raises(mf.MainframeError, match="plaintext"):
        backend.connect("ibmi.example", 23, session_type="5250")


def test_port_992_is_not_treated_as_tls() -> None:
    backend = mf._Tn5250Backend()
    with pytest.raises(mf.MainframeError, match="992 is the conventional TLS port"):
        backend.connect("ibmi.example", 992, session_type="5250")


def test_loopback_plaintext_is_allowed() -> None:
    # A loopback host is the local end of a tunnel or a mock — exempt.
    mf._require_transport_policy(
        "127.0.0.1", 23, tls=False, allow_plaintext=False, backend="tn5250"
    )
    mf._require_transport_policy(
        "localhost", 23, tls=False, allow_plaintext=False, backend="tn5250"
    )


def test_s3270_cafile_on_a_build_without_support_is_a_clear_error(monkeypatch) -> None:
    # The Windows Schannel ws3270 build has no -cafile option and refuses to
    # start when handed one. Rather than silently drop the CA (validating
    # against a different trust store), the backend raises a clear error.
    monkeypatch.setattr(mf, "_s3270_supports_cafile", lambda binary: False)
    backend = mf._S3270Backend.__new__(mf._S3270Backend)
    backend._binary = "ws3270.exe"
    backend._model = "3279-4"
    backend._codepage = None
    backend._extra_args = []
    backend._tls_cafile = r"C:\ca\corp.pem"
    backend._proc = None
    with pytest.raises(mf.MainframeError, match="does not support -cafile"):
        backend._spawn()


def test_s3270_cafile_is_passed_when_the_build_supports_it(monkeypatch) -> None:
    monkeypatch.setattr(mf, "_s3270_supports_cafile", lambda binary: True)
    captured: dict = {}
    monkeypatch.setattr(
        mf.subprocess, "Popen", lambda args, **kw: captured.setdefault("args", args) or object()
    )
    monkeypatch.setattr(mf.sys, "platform", "linux")
    backend = mf._S3270Backend.__new__(mf._S3270Backend)
    backend._binary = "s3270"
    backend._model = "3279-4"
    backend._codepage = None
    backend._extra_args = []
    backend._tls_cafile = "/etc/corp-ca.pem"
    backend._proc = None
    backend._spawn()
    assert "-cafile" in captured["args"]
    assert "/etc/corp-ca.pem" in captured["args"]


def test_a_non_verifying_tls_context_is_refused() -> None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with pytest.raises(mf.MainframeError, match="does not verify the server"):
        mf._Tn5250Backend(tls=True, tls_context=ctx)


class _TLSEchoServer:
    """One-shot TLS server on 127.0.0.1 for the handshake test."""

    def __init__(self) -> None:
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(certfile=str(_CERT), keyfile=str(_KEY))
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self._sock.settimeout(5.0)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return self._sock.getsockname()[1]

    def _run(self) -> None:
        try:
            client, _ = self._sock.accept()
        except OSError:
            return
        try:
            tls = self._ctx.wrap_socket(client, server_side=True)
            try:
                tls.recv(64)
            except OSError:
                pass
            tls.close()
        except (ssl.SSLError, OSError):
            # A TLS 1.3 client that closes right after its Finished message
            # aborts the server mid-handshake; that is fine here — the client
            # side has already proven it verified the certificate.
            pass

    def close(self) -> None:
        # Wait for the server thread to finish its handshake bookkeeping before
        # the caller inspects it — otherwise the count is read mid-handshake.
        self._thread.join(timeout=3.0)
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.mark.skipif(not _CERT.is_file(), reason="TLS fixture certificate missing")
def test_tn5250_tls_handshake_verifies_the_certificate(monkeypatch) -> None:
    server = _TLSEchoServer()
    try:
        backend = mf._Tn5250Backend(tls=True, tls_cafile=str(_CERT))
        # Stop right after the handshake — the 5250 protocol itself is covered
        # elsewhere; this test is about the transport.
        monkeypatch.setattr(backend, "_negotiate", lambda: None)
        monkeypatch.setattr(backend, "_read_records", lambda timeout: [])
        # connect() only returns if the certificate verified against the
        # fixture CA and the host name matched — the security property.
        backend.connect("127.0.0.1", server.port, session_type="5250")
        assert isinstance(backend._sock, ssl.SSLSocket)
        assert backend._sock.version() is not None
        backend.disconnect()
    finally:
        server.close()


@pytest.mark.skipif(not _CERT.is_file(), reason="TLS fixture certificate missing")
def test_tn5250_tls_rejects_an_untrusted_certificate_without_falling_back() -> None:
    server = _TLSEchoServer()
    try:
        # Default trust store — the throwaway cert is unknown, so verification
        # must fail and the connection must NOT drop to plaintext.
        backend = mf._Tn5250Backend(tls=True)
        with pytest.raises(mf.MainframeError, match="TLS handshake"):
            backend.connect("127.0.0.1", server.port, session_type="5250")
        assert backend._sock is None
    finally:
        server.close()


# --------------------------------------------------------------------------- #
# KAN-469 — secret hygiene                                                     #
# --------------------------------------------------------------------------- #


def test_s3270_trace_names_the_action_not_the_typed_text(caplog) -> None:
    backend = _CapturingS3270()
    backend._trace = True
    with caplog.at_level("INFO", logger="dolphin_desktop.mainframe"):
        described = mf._describe_s3270_action('String("SYNTHETIC_CANARY_PW")')
    assert "SYNTHETIC_CANARY_PW" not in described
    assert described.startswith("String(")


def test_secret_round_trips_to_the_backend_but_is_masked_in_logs() -> None:
    backend = _CapturingS3270()
    backend.send_string(Secret("SYNTHETIC_CANARY_PW"))
    # The emulator receives the real characters...
    assert "SYNTHETIC_CANARY_PW" in backend.sent[0]
    # ...but the value is registered for redaction everywhere else.
    assert "SYNTHETIC_CANARY_PW" not in _redact("password was SYNTHETIC_CANARY_PW here")


def test_hllapi_payload_functions_are_not_dumped(caplog) -> None:
    from unittest.mock import Mock

    backend = mf._HLLAPIBackend(_hllapi_fn=Mock())
    backend._trace = True
    with caplog.at_level("INFO", logger="dolphin_desktop.mainframe"):
        backend.send_string("SYNTHETIC_CANARY_PW")
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "SYNTHETIC_CANARY_PW" not in joined
