"""Direct WSS/RFB client, without browser, DOM or sensitive console output."""

import asyncio
import ipaddress
import socket
from contextlib import suppress
from io import BytesIO
from urllib.parse import urlsplit

import asyncvnc
from PIL import Image
from websockets.asyncio.client import connect


class VNCTransport:
    def __init__(self, url: str, password: str, allow_private: bool = False, tls_context=None,
                 text_input: str = "keys"):
        if text_input not in {"keys", "clipboard_utf8", "clipboard_latin1"}:
            raise ValueError("Unsupported text input mode")
        self.text_input = text_input
        self._url, self._password = url, password
        self._allow_private = allow_private
        self._tls_context = tls_context
        self._client = None
        self._server = None
        self._writers = set()
        self._relays = set()

    async def _socket(self):
        """Validate resolved IPs and pin the socket to prevent DNS re-resolution."""
        parsed = urlsplit(self._url)
        host, port = parsed.hostname, parsed.port or 443
        if parsed.scheme != "wss" or not host or parsed.username or parsed.password:
            raise ValueError("Invalid desktop endpoint")
        loop = asyncio.get_running_loop()
        addresses = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses:
            raise ConnectionError("Desktop resolution failed")
        if not self._allow_private and any(
            not ipaddress.ip_address(a[4][0]).is_global for a in addresses
        ):
            raise ValueError("Desktop address not permitted")
        family, socktype, protocol, _, address = addresses[0]
        sock = socket.socket(family, socktype, protocol)
        sock.setblocking(False)
        try:
            await asyncio.wait_for(loop.sock_connect(sock, address), 10)
        except BaseException:
            sock.close()
            raise
        return sock, host

    async def _relay(self, reader, writer):
        task = asyncio.current_task()
        self._relays.add(task)
        self._writers.add(writer)
        pumps = []
        sock = None
        try:
            sock, host = await self._socket()
            async with connect(
                self._url,
                **({"ssl": self._tls_context} if self._tls_context is not None else {}),
                sock=sock,
                server_hostname=host,
                subprotocols=["binary"],
                compression=None,
                max_size=8 * 1024 * 1024,
                open_timeout=15,
                close_timeout=1,
            ) as ws:

                async def upstream():
                    while data := await reader.read(65536):
                        await ws.send(data)

                async def downstream():
                    async for data in ws:
                        if not isinstance(data, bytes):
                            raise ValueError("RFB requires binary messages")
                        writer.write(data)
                        await writer.drain()

                pumps = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
                done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
                for finished in done:
                    finished.result()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Endpoint and authentication errors must never be forwarded verbatim.
            pass
        finally:
            for pump in pumps:
                pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            if sock is not None:
                sock.close()
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()
            self._writers.discard(writer)
            self._relays.discard(task)

    async def open(self):
        try:
            self._server = await asyncio.start_server(self._relay, "127.0.0.1", 0)
            port = self._server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            self._writers.add(writer)
            self._client = await asyncio.wait_for(
                asyncvnc.Client.create(reader, writer, password=self._password), 20
            )
        except BaseException:
            await self.close()
            raise

    async def capture(self) -> tuple[bytes, int, int]:
        frame = await asyncio.wait_for(self._client.screenshot(), 10)
        image = Image.fromarray(frame).convert("RGB")
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue(), image.width, image.height

    async def action(self, args: dict):
        kind = args["kind"]
        if kind in {"click", "double_click"}:
            x, y = args["x"], args["y"]
            if not 0 <= x < self._client.video.width or not 0 <= y < self._client.video.height:
                raise ValueError("Coordinates outside framebuffer")
            for i in range(2 if kind == "double_click" else 1):
                self._client.mouse.move(x, y)
                with self._client.mouse.hold():
                    await self._client.drain()
                    await asyncio.sleep(args["hold_ms"] / 1000)
                await self._client.drain()
                if kind == "double_click" and i == 0:
                    await asyncio.sleep(0.15)
        elif kind == "press_key":
            self._validate_keys(args["keys"])
            with self._client.keyboard.hold(*args["keys"]):
                await self._client.drain()
                await asyncio.sleep(0.15)
            await self._client.drain()
        elif kind == "replace_text":
            targets = args.get("text_targets") or [args]
            if len(targets) > 8 or (args.get("text_targets") and (args.get("text") or args.get("x") is not None)):
                raise ValueError("Invalid text target group")
            prepared = []
            for target in targets:
                if (target.get("x") is None) != (target.get("y") is None):
                    raise ValueError("Text target requires both coordinates")
                if target.get("x") is not None and not (
                    0 <= target["x"] < self._client.video.width and 0 <= target["y"] < self._client.video.height
                ):
                    raise ValueError("Coordinates outside framebuffer")
                data = None
                if self.text_input == "keys":
                    self._validate_keys(target["text"])
                else:
                    data = target["text"].encode("utf-8" if self.text_input == "clipboard_utf8" else "latin-1")
                prepared.append((target, data))
            for args, data in prepared:
                if args.get("x") is not None:
                    self._client.mouse.move(args["x"], args["y"])
                    with self._client.mouse.hold():
                        await self._client.drain()
                        await asyncio.sleep(0.18)
                    await self._client.drain()
                    await asyncio.sleep(0.3)
                if data is not None:
                    self._client.clipboard.writer.write(
                        b"\x06\x00\x00\x00" + len(data).to_bytes(4, "big") + data)
                    await self._client.drain()
                    await asyncio.sleep(0.3)
                with self._client.keyboard.hold("Ctrl", "a"):
                    await self._client.drain()
                    await asyncio.sleep(0.15)
                await self._client.drain()
                if data is None:
                    self._client.keyboard.write(args["text"])
                else:
                    with self._client.keyboard.hold("Ctrl", "v"):
                        await self._client.drain()
                        await asyncio.sleep(0.15)
                await self._client.drain()
        elif kind == "scroll":
            for _ in range(args["amount"]):
                with self._client.mouse.hold(3 if args["direction"] == "up" else 4):
                    await self._client.drain()
                    await asyncio.sleep(0.1)
                await self._client.drain()
        else:
            raise ValueError("Unsupported tool")
        await asyncio.sleep(1.0)

    @staticmethod
    def _validate_keys(keys):
        if any(key not in asyncvnc.key_codes for key in keys):
            raise ValueError("Unsupported key or character")

    async def close(self):
        self._client = None
        listener = self._server
        self._server = None
        if listener:
            listener.close()
        for writer in list(self._writers):
            writer.close()
        relays = list(self._relays)
        for relay in relays:
            relay.cancel()
        await asyncio.gather(*relays, return_exceptions=True)
        for writer in list(self._writers):
            with suppress(Exception):
                await writer.wait_closed()
        self._writers.clear()
        if listener:
            await listener.wait_closed()
