"""Notebook-only VNC tools: AsyncVNC via a private loopback WebSocket bridge.

This is a transport prototype, not the production agent or cancellation design.
No Playwright, DOM access, reset/check or LLM provider is involved.
"""

import asyncio
import base64
import json
import time
from contextlib import suppress
from io import BytesIO
from pathlib import Path

import asyncvnc
from PIL import Image
from websockets.asyncio.client import connect


class VNCProbe:
    def __init__(self, url: str, password: str, *, seconds: int = 180):
        self._url = url
        self._password = password
        self._seconds = seconds
        self._deadline = 0.0
        self._client = None
        self._server = None
        self._watchdog = None
        self._bridges = set()
        self._writers = set()
        self._lock = asyncio.Lock()
        self.events = []
        self.last_image = None
        self._bridge_error = None

    def _log(self, command: str, **result):
        event = {"command": command, **result}
        self.events.append(event)
        print(json.dumps(event, ensure_ascii=False))
        return event

    async def _relay(self, reader, writer):
        task = asyncio.current_task()
        self._bridges.add(task)
        self._writers.add(writer)
        pumps = []
        try:
            async with connect(self._url, subprotocols=["binary"], compression=None,
                               max_size=None, open_timeout=15) as ws:
                async def upstream():
                    while data := await reader.read(65536):
                        await ws.send(data)

                async def downstream():
                    async for data in ws:
                        if not isinstance(data, bytes):
                            raise ValueError("Expected binary RFB transport")
                        writer.write(data)
                        await writer.drain()

                pumps = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
                done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
                for item in done:
                    item.result()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Class only: exceptions can contain destination or credentials.
            self._bridge_error = type(exc).__name__
        finally:
            for item in pumps:
                item.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()
            self._writers.discard(writer)
            self._bridges.discard(task)

    async def connect(self):
        if self._client is not None:
            raise RuntimeError("Disconnect the existing session first")
        self._deadline = time.monotonic() + self._seconds
        self._watchdog = asyncio.create_task(self._expire())
        try:
            self._server = await asyncio.start_server(self._relay, "127.0.0.1", 0)
            port = self._server.sockets[0].getsockname()[1]
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            self._writers.add(writer)
            self._client = await asyncio.wait_for(
                asyncvnc.Client.create(reader, writer, password=self._password), 20)
            self._check()
            return self._log("connect", ok=True,
                             width=self._client.video.width, height=self._client.video.height,
                             transport="WSS/RFB · sin navegador")
        except Exception as exc:
            error_type = self._bridge_error or type(exc).__name__
            await self.disconnect()
            raise RuntimeError(f"VNC connection failed ({error_type}); credentials hidden") from None

    async def _expire(self):
        await asyncio.sleep(max(0, self._deadline - time.monotonic()))
        self._log("deadline", ok=False, reason="Sesión de prueba vencida")
        await self.disconnect()

    def _check(self):
        if self._client is None or time.monotonic() >= self._deadline:
            raise RuntimeError("Sesión cerrada o vencida; conectar nuevamente")

    async def observe(self):
        async with self._lock:
            self._check()
            start = time.monotonic()
            try:
                frame = await asyncio.wait_for(self._client.screenshot(),
                                              min(15, self._deadline - start))
            except Exception as exc:
                await self.disconnect()
                raise RuntimeError(f"Capture failed ({type(exc).__name__}); session closed") from None
            self._check()
            self.last_image = Image.fromarray(frame).convert("RGB")
            self._log("observe", ok=True, width=self.last_image.width,
                      height=self.last_image.height,
                      elapsed_ms=round((time.monotonic() - start) * 1000))
            return self.last_image

    async def click(self, x: int, y: int, *, hold_ms: int = 180):
        async with self._lock:
            self._check()
            if not (0 <= x < self._client.video.width and 0 <= y < self._client.video.height):
                raise ValueError("Coordenadas fuera del escritorio")
            if not 100 <= hold_ms <= 1000:
                raise ValueError("hold_ms debe estar entre 100 y 1000")
            self._client.mouse.move(x, y)
            with self._client.mouse.hold():
                await self._client.drain()
                await asyncio.sleep(hold_ms / 1000)
            await self._client.drain()
            self._log("click", ok=True, x=x, y=y, hold_ms=hold_ms)
            await asyncio.sleep(1.1)

    async def press_key(self, *keys: str):
        async with self._lock:
            self._check()
            with self._client.keyboard.hold(*keys):
                await self._client.drain()
                await asyncio.sleep(0.15)
            await self._client.drain()
            self._log("press_key", ok=True, keys=list(keys))
            await asyncio.sleep(1.1)

    async def move_pointer(self, x: int, y: int):
        async with self._lock:
            self._check()
            if not (0 <= x < self._client.video.width and 0 <= y < self._client.video.height):
                raise ValueError("Coordenadas fuera del escritorio")
            self._client.mouse.move(x, y)
            await self._client.drain()
            self._log("move_pointer", ok=True, x=x, y=y)
            await asyncio.sleep(1.1)

    async def replace_text(self, text: str):
        """Only use on a visually verified field; never on an unknown focus."""
        async with self._lock:
            self._check()
            if any(character not in asyncvnc.key_codes for character in text):
                raise ValueError("Texto contiene caracteres no soportados")
            self._client.keyboard.press("Ctrl", "a")
            self._client.keyboard.write(text)
            await self._client.drain()
            # No contents logged; text may be private.
            self._log("replace_text", ok=True, characters=len(text))
            await asyncio.sleep(1.1)

    def image_for_model(self):
        if self.last_image is None:
            raise RuntimeError("Observar la pantalla primero")
        output = BytesIO()
        self.last_image.save(output, format="PNG")
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()

    def save_image(self, destination: Path):
        if self.last_image is None:
            raise RuntimeError("Observar la pantalla primero")
        destination.parent.mkdir(parents=True, exist_ok=True)
        self.last_image.save(destination)
        self._log("save_image", ok=True, file=destination.name)

    async def disconnect(self):
        # Revoke first; also closes transports for partially established sessions.
        self._client = None
        for writer in list(self._writers):
            writer.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        bridges = list(self._bridges)
        for task in bridges:
            task.cancel()
        if bridges:
            await asyncio.gather(*bridges, return_exceptions=True)
        for writer in list(self._writers):
            with suppress(Exception):
                await writer.wait_closed()
        self._writers.clear()
        if self._watchdog is not None and self._watchdog is not asyncio.current_task():
            self._watchdog.cancel()
            await asyncio.gather(self._watchdog, return_exceptions=True)
        self._watchdog = None
        self._log("disconnect", ok=True)
