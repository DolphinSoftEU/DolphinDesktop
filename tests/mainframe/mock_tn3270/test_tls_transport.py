"""End-to-end transport-security test for KAN-468.

A real mock terminal server on 127.0.0.1 wraps its socket in TLS using the
checked-in throwaway certificate, completes the TN5250 telnet negotiation,
and sends one Write-To-Display record. The pure-Python ``tn5250`` backend is
then driven against it with ``tls=True``:

* with the fixture CA trusted, the handshake verifies and the screen is read
  over the encrypted channel — the full connect → TLS → negotiate → parse
  path exercised end to end;
* without the CA (system trust store), verification fails and the connection
  raises instead of falling back to plaintext.

The ``tn5250`` backend is used rather than ``s3270`` because it owns its TLS
in Python and verifies against a PEM ``tls_cafile``; the Windows Schannel
``ws3270`` build validates against the Windows certificate store and cannot
trust a throwaway PEM without importing it as an admin.
"""

from __future__ import annotations

import socket
import ssl
import threading
import time
from pathlib import Path

import pytest

from dolphin_desktop import Desktop, MainframeError

_TLS_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "tls"
_CERT = _TLS_DIR / "localhost.crt"
_KEY = _TLS_DIR / "localhost.key"

_IAC, _EOR = 0xFF, 0xEF
_BANNER = "DOLPHIN TLS MOCK 5250"


def _wtd_record(banner: str) -> bytes:
    """Build one IAC-EOR-framed 5250 Write-To-Display record.

    Layout the ``_Tn5250Backend`` parser expects: 10-byte GDS header, then
    ``ESC(0x04) WTD(0x11)``, two control-character bytes, an SBA order placing
    the cursor at row 3 col 2, then the banner in EBCDIC (cp037).
    """
    body = bytes(10)  # GDS header — parser starts scanning at offset 10
    body += bytes([0x04, 0x11])  # ESC + Write-To-Display
    body += bytes([0x00, 0x00])  # 2 control-character bytes
    body += bytes([0x11, 3, 2])  # SBA -> row 3, col 2
    body += banner.encode("cp037")
    return body.replace(b"\xff", b"\xff\xff") + bytes([_IAC, _EOR])


class _Tls5250Mock:
    """One-shot TLS TN5250 mock server on 127.0.0.1."""

    def __init__(self, banner: str = _BANNER) -> None:
        self._record = _wtd_record(banner)
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(certfile=str(_CERT), keyfile=str(_KEY))
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self._sock.settimeout(8.0)
        self.tls_completed = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return self._sock.getsockname()[1]

    def _run(self) -> None:
        try:
            raw, _ = self._sock.accept()
        except OSError:
            return
        try:
            tls = self._ctx.wrap_socket(raw, server_side=True)
        except (ssl.SSLError, OSError):
            # A client that refused our certificate aborts the handshake here.
            return
        self.tls_completed = True
        try:
            tls.settimeout(1.0)
            # Drain the client's telnet negotiation, then send the screen.
            try:
                tls.recv(4096)
            except OSError:
                pass
            tls.sendall(self._record)
            # Keep the connection open ~6s so the client can read the record
            # across its connect-time read and any later polling reads. Break
            # early once the client disconnects.
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                try:
                    if tls.recv(4096) == b"":
                        break
                except (TimeoutError, OSError):
                    continue
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def close(self) -> None:
        self._thread.join(timeout=3.0)
        try:
            self._sock.close()
        except OSError:
            pass


@pytest.mark.skipif(not _CERT.is_file(), reason="TLS fixture certificate missing")
def test_tn5250_reads_a_screen_over_verified_tls() -> None:
    mock = _Tls5250Mock()
    term = None
    try:
        term = Desktop().mainframe(
            host="127.0.0.1",
            port=mock.port,
            session_type="5250",
            backend="tn5250",
            tls=True,
            tls_cafile=str(_CERT),
            timeout=8,
        )
        # Poll rather than read once: the record may land just after connect
        # returns. wait_for_text reads records on each poll and raises if the
        # banner never arrives.
        term.wait_for_text(_BANNER, timeout=6)
    finally:
        if term is not None:
            try:
                term.disconnect()
            except Exception:
                pass
        mock.close()
    assert mock.tls_completed, "the server never completed a TLS handshake"


@pytest.mark.skipif(not _CERT.is_file(), reason="TLS fixture certificate missing")
def test_tn5250_tls_rejects_an_untrusted_certificate_without_fallback() -> None:
    mock = _Tls5250Mock()
    try:
        # No tls_cafile -> the throwaway cert is unknown to the system trust
        # store, so verification must fail and the session must NOT drop to
        # plaintext.
        with pytest.raises(MainframeError, match="TLS handshake"):
            Desktop().mainframe(
                host="127.0.0.1",
                port=mock.port,
                session_type="5250",
                backend="tn5250",
                tls=True,
                timeout=5,
            )
    finally:
        mock.close()


@pytest.mark.skipif(not _CERT.is_file(), reason="TLS fixture certificate missing")
def test_tn5250_plaintext_to_a_tls_port_does_not_leak_a_session(monkeypatch) -> None:
    """A plaintext client against the TLS mock must not end up 'connected'.

    Loopback is exempt from the plaintext refusal, so this connects in
    plaintext; the server speaks TLS, so the telnet negotiation is garbage and
    no screen is ever read. The point is that the plaintext bytes never reach
    a real host in cleartext expecting them — here the handshake simply never
    forms a usable session.
    """
    mock = _Tls5250Mock()
    term = None
    try:
        # Plaintext (no tls=) to a loopback host is allowed by policy, but the
        # TLS server will not understand it; the screen stays empty.
        term = Desktop().mainframe(
            host="127.0.0.1",
            port=mock.port,
            session_type="5250",
            backend="tn5250",
            timeout=3,
        )
        # No usable 5250 screen is produced from a TLS server over plaintext.
        assert _BANNER not in term.text()
    except (MainframeError, OSError):
        # The TLS server resets the plaintext connection during negotiation;
        # a connect/read error here is the expected outcome, not a leak.
        pass
    finally:
        if term is not None:
            try:
                term.disconnect()
            except Exception:
                pass
        mock.close()
    assert not mock.tls_completed, "a plaintext client must not complete a TLS handshake"
