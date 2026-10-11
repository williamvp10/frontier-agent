"""Actual TLS WebSocket / RFB wire tests against a disposable synthetic desktop."""

import asyncio
import ipaddress
import ssl
import struct
import time
import zlib
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from io import BytesIO

import asyncvnc
import pytest
from cryptography import x509
from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from PIL import Image
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from app.desktop.transport import VNCTransport


class SyntheticDesktop:
    """Minimal authenticated RFB server: only frames and recorded input, no app fixtures."""

    width, height = 32, 24
    password = "testpass"

    def __init__(self):
        self.inputs = []
        self.disconnected = asyncio.Event()
        self.authentication_ok = False
        self.clipboards = []

    async def handle(self, ws):
        buffer = bytearray()

        async def read(size):
            while len(buffer) < size:
                message = await ws.recv()
                assert isinstance(message, bytes)
                buffer.extend(message)
            data = bytes(buffer[:size])
            del buffer[:size]
            return data

        try:
            await ws.send(b"RFB 003.008\n")
            assert await read(12) == b"RFB 003.008\n"
            await ws.send(b"\x01\x02")  # one auth type: VNC challenge
            assert await read(1) == b"\x02"
            challenge = bytes(range(16))
            await ws.send(challenge)
            key = bytes(int(f"{n:08b}"[::-1], 2) for n in self.password.encode())
            encryptor = Cipher(TripleDES(key * 3), modes.ECB()).encryptor()
            self.authentication_ok = await read(16) == encryptor.update(challenge)
            await ws.send(struct.pack("!I", 0 if self.authentication_ok else 1))
            if not self.authentication_ok:
                return
            assert await read(1) == b"\x01"  # shared client init
            pixel_format = bytes.fromhex("2018000100ff00ff00ff000810000000")
            name = b"Synthetic desktop"
            await ws.send(
                struct.pack("!HH", self.width, self.height)
                + pixel_format
                + struct.pack("!I", len(name))
                + name
            )
            compressor = zlib.compressobj()
            while True:
                message_type = (await read(1))[0]
                if message_type == 0:  # SetPixelFormat
                    await read(19)
                elif message_type == 2:  # SetEncodings
                    header = await read(3)
                    await read(struct.unpack("!H", header[1:])[0] * 4)
                elif message_type == 3:  # FramebufferUpdateRequest
                    await read(9)
                    raw = bytes([12, 34, 56, 255]) * self.width * self.height
                    compressed = compressor.compress(raw) + compressor.flush(zlib.Z_SYNC_FLUSH)
                    rectangle = struct.pack("!HHHHi", 0, 0, self.width, self.height, 6)
                    await ws.send(
                        b"\x00\x00\x00\x01"
                        + rectangle
                        + struct.pack("!I", len(compressed))
                        + compressed
                    )
                elif message_type == 4:  # KeyEvent
                    payload = await read(7)
                    self.inputs.append(
                        ("key", time.monotonic(), payload[0], struct.unpack("!I", payload[3:])[0])
                    )
                elif message_type == 5:  # PointerEvent
                    payload = await read(5)
                    self.inputs.append(
                        (
                            "pointer",
                            time.monotonic(),
                            payload[0],
                            *struct.unpack("!HH", payload[1:]),
                        )
                    )
                elif message_type == 6:  # ClientCutText: exactly three padding bytes
                    assert await read(3) == b"\x00\x00\x00"
                    size = struct.unpack("!I", await read(4))[0]
                    assert size <= 65536
                    self.clipboards.append(await read(size))
                else:
                    raise AssertionError(f"Unexpected RFB event: {message_type}")
        except ConnectionClosed:
            pass
        finally:
            self.disconnected.set()


def tls_contexts(tmp_path):
    """Ephemeral localhost certificate is explicitly trusted; verification stays enabled."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "synthetic-ca.pem", tmp_path / "synthetic-key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    client = ssl.create_default_context(cafile=str(cert_path))
    assert client.verify_mode == ssl.CERT_REQUIRED and client.check_hostname
    return server, client


@asynccontextmanager
async def desktop_server(tmp_path):
    server_tls, client_tls = tls_contexts(tmp_path)
    desktop = SyntheticDesktop()
    async with serve(
        desktop.handle, "127.0.0.1", 0, ssl=server_tls, subprotocols=["binary"]
    ) as server:
        port = server.sockets[0].getsockname()[1]
        yield desktop, f"wss://127.0.0.1:{port}/websockify", client_tls


@pytest.mark.integration
async def test_wss_rfb_auth_capture_click_keys_unicode_and_disconnect(tmp_path):
    async with desktop_server(tmp_path) as (desktop, url, client_tls):
        transport = VNCTransport(url, desktop.password, allow_private=True, tls_context=client_tls)
        try:
            await asyncio.wait_for(transport.open(), 5)
            png, width, height = await transport.capture()
            assert desktop.authentication_ok
            assert (width, height) == (32, 24)
            image = Image.open(BytesIO(png))
            assert image.getpixel((0, 0)) == (12, 34, 56)
            await transport.action({"kind": "click", "x": 5, "y": 6, "hold_ms": 180})
            pointer = [event for event in desktop.inputs if event[0] == "pointer"]
            pressed = next(event for event in pointer if event[2] == 1)
            released = next(event for event in pointer if event[2] == 0 and event[1] > pressed[1])
            assert pressed[3:] == released[3:] == (5, 6)
            assert released[1] - pressed[1] >= 0.100
            await transport.action({"kind": "press_key", "keys": ["Tab"]})
            await transport.action({"kind": "replace_text", "text": "Bogotá ñ"})
            keys = [(e[2], e[3]) for e in desktop.inputs if e[0] == "key"]
            tab, ctrl, a = (asyncvnc.key_codes[k] for k in ("Tab", "Ctrl", "a"))
            assert keys[:2] == [(1, tab), (0, tab)]
            assert keys[2:6] == [(1, ctrl), (1, a), (0, a), (0, ctrl)]
            typed = [
                (pressed, code)
                for char in "Bogotá ñ"
                for pressed, code in ((1, asyncvnc.key_codes[char]), (0, asyncvnc.key_codes[char]))
            ]
            assert keys[6:] == typed
            png2, _, _ = await transport.capture()
            assert png2 == png  # persistent zlib decompressor handles subsequent frame
        finally:
            await asyncio.wait_for(transport.close(), 3)
        await asyncio.wait_for(desktop.disconnected.wait(), 2)
        assert transport._server is None
        assert not transport._writers and not transport._relays


@pytest.mark.integration
@pytest.mark.parametrize("mode,text,encoding", [
    ("clipboard_utf8", "Árbol Ω", "utf-8"),
    ("clipboard_latin1", "Árbol ñ", "latin-1"),
])
async def test_explicit_clipboard_encoding_preserves_text_without_character_key_events(tmp_path, mode, text, encoding):
    async with desktop_server(tmp_path) as (desktop, url, client_tls):
        transport = VNCTransport(url, desktop.password, allow_private=True,
                                 tls_context=client_tls, text_input=mode)
        try:
            await transport.open()
            await transport.capture()
            await transport.action({"kind": "replace_text", "text": text, "x": 5, "y": 5})
            assert any(e[0] == "pointer" for e in desktop.inputs)
            assert desktop.clipboards == [text.encode(encoding)]
            keys = [(e[2], e[3]) for e in desktop.inputs if e[0] == "key"]
            ctrl, a, v = (asyncvnc.key_codes[k] for k in ("Ctrl", "a", "v"))
            assert keys == [(1, ctrl), (1, a), (0, a), (0, ctrl),
                            (1, ctrl), (1, v), (0, v), (0, ctrl)]
            await transport.capture()
        finally:
            await transport.close()
        await asyncio.wait_for(desktop.disconnected.wait(), 2)


@pytest.mark.integration
async def test_default_transport_rejects_private_destination_before_connection(tmp_path):
    async with desktop_server(tmp_path) as (desktop, url, client_tls):
        transport = VNCTransport(url, desktop.password, tls_context=client_tls)
        with pytest.raises(ValueError, match="not permitted"):
            await transport._socket()
        assert not desktop.authentication_ok


@pytest.mark.integration
async def test_wss_rfb_rejects_wrong_password_and_closes(tmp_path):
    async with desktop_server(tmp_path) as (desktop, url, client_tls):
        transport = VNCTransport(url, "wrongpwd", allow_private=True, tls_context=client_tls)
        with pytest.raises(PermissionError, match="Auth failed"):
            await asyncio.wait_for(transport.open(), 5)
        await asyncio.wait_for(transport.close(), 3)
        await asyncio.wait_for(desktop.disconnected.wait(), 2)
        assert not desktop.authentication_ok and not desktop.inputs
        assert transport._server is None
        assert not transport._writers and not transport._relays


@pytest.mark.integration
async def test_wss_default_tls_rejects_untrusted_certificate(tmp_path):
    async with desktop_server(tmp_path) as (desktop, url, _):
        transport = VNCTransport(url, desktop.password, allow_private=True)
        with pytest.raises((ValueError, ConnectionError, asyncio.IncompleteReadError)):
            await asyncio.wait_for(transport.open(), 5)
        await asyncio.wait_for(transport.close(), 3)
        assert not desktop.authentication_ok
        assert transport._server is None
        assert not transport._writers and not transport._relays


@pytest.mark.integration
async def test_text_group_focuses_each_field_and_never_sends_submit_keys(tmp_path):
    async with desktop_server(tmp_path) as (desktop, url, client_tls):
        transport = VNCTransport(url, desktop.password, allow_private=True,
                                 tls_context=client_tls, text_input="clipboard_utf8")
        try:
            await transport.open()
            await transport.capture()
            await transport.action({"kind": "replace_text", "text": "", "text_targets": [
                {"x": 5, "y": 5, "text": "Synthetic A"},
                {"x": 10, "y": 10, "text": "Synthetic B"}]})
            assert desktop.clipboards == [b"Synthetic A", b"Synthetic B"]
            clicks = [(e[3], e[4]) for e in desktop.inputs if e[0] == "pointer" and e[2] == 1]
            assert clicks == [(5, 5), (10, 10)]
            assert not any(e[0] == "key" and e[3] == asyncvnc.key_codes["Return"] for e in desktop.inputs)
        finally:
            await transport.close()
        await asyncio.wait_for(desktop.disconnected.wait(), 2)
