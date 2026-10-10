"""Spawned socket owner: a wall-clock-independent hard stop survives a blocked model."""

import asyncio
import multiprocessing as mp
import os
import threading
import time
from datetime import datetime
from uuid import UUID

from app.core.observability import observed, update_current
from app.desktop.transport import VNCTransport
from app.domain.contracts import utcnow


class GatewayError(RuntimeError):
    pass


def _gateway_process(pipe, url, password, session_id, epoch, seconds, allow_private, factory):
    stop = threading.Event()

    # This thread closes the entire socket owner even if its asyncio loop hangs.
    def hard_stop():
        if not stop.wait(max(0, seconds)):
            os._exit(90)

    threading.Thread(target=hard_stop, daemon=True).start()
    cutoff = time.monotonic() + max(0, seconds)

    async def serve():
        transport = factory(url, password, allow_private)
        try:
            await transport.open()
            pipe.send({"ok": True})
            while time.monotonic() < cutoff:
                command = await asyncio.to_thread(pipe.recv)
                if command["kind"] == "close":
                    await transport.close()
                    pipe.send({"ok": True, "closed": True})
                    return
                if command.get("session_id") != session_id or command.get("epoch") != epoch:
                    pipe.send({"ok": False, "code": "stale_session"})
                    continue
                if command["kind"] == "observe":
                    image, width, height = await transport.capture()
                    if time.monotonic() >= cutoff:
                        return
                    pipe.send({"ok": True, "image": image, "width": width, "height": height})
                elif command["kind"] == "execute":
                    # Worst-case allowance covers input release; fail closed near deadline.
                    if cutoff - time.monotonic() <= 3.0:
                        pipe.send({"ok": False, "code": "deadline"})
                        continue
                    await transport.action(command["args"])
                    pipe.send({"ok": True})
                else:
                    pipe.send({"ok": False, "code": "unsupported_command"})
        except BaseException:
            try:
                pipe.send({"ok": False, "code": "transport_error"})
            except (BrokenPipeError, EOFError, OSError):
                pass
        finally:
            await transport.close()
            pipe.close()
            stop.set()

    asyncio.run(serve())


class ProcessGateway:
    def __init__(
        self, session_id: UUID, epoch: int, cutoff_at: datetime, *, transport_factory=VNCTransport
    ):
        self.session_id, self.epoch = str(session_id), epoch
        self.cutoff_at = cutoff_at
        self._factory = transport_factory
        self._process = None
        self._pipe = None
        self._lock = asyncio.Lock()
        self.closed = False

    @observed("connect-vnc", as_type="tool", counter="vnc_calls")
    async def open(self, url: str, password: str, *, allow_private=False):
        seconds = (self.cutoff_at - utcnow()).total_seconds()
        if seconds <= 3:
            raise GatewayError("No safe execution time remains")
        context = mp.get_context("spawn")
        parent, child = context.Pipe()
        self._pipe = parent
        self._process = context.Process(
            target=_gateway_process,
            args=(
                child,
                url,
                password,
                self.session_id,
                self.epoch,
                seconds,
                allow_private,
                self._factory,
            ),
            daemon=True,
        )
        self._process.start()
        child.close()
        try:
            await self._receive(22)
        except BaseException:
            await self.close_and_confirm()
            raise

    def _blocking_receive(self, timeout):
        if self._pipe is None or not self._pipe.poll(timeout):
            raise GatewayError("Gateway response timed out")
        try:
            result = self._pipe.recv()
        except (EOFError, OSError):
            raise GatewayError("Gateway stopped") from None
        if not result.get("ok"):
            raise GatewayError(result.get("code", "gateway_error"))
        return result

    async def _receive(self, timeout=12):
        return await asyncio.to_thread(self._blocking_receive, timeout)

    async def request(self, kind: str, **fields):
        async with self._lock:
            if self.closed or not self._process or not self._process.is_alive():
                raise GatewayError("Gateway closed")
            if utcnow() >= self.cutoff_at:
                raise GatewayError("Action deadline reached")
            try:
                self._pipe.send(
                    {"kind": kind, "session_id": self.session_id, "epoch": self.epoch, **fields}
                )
                return await self._receive()
            except (BrokenPipeError, EOFError, OSError):
                raise GatewayError("Gateway stopped") from None

    @observed("close-vnc", as_type="tool", counter="vnc_calls")
    async def close_and_confirm(self) -> dict:
        self.closed = True  # Revoke before any waits; in-flight IPC is no longer useful.
        process = self._process
        if process:
            # A busy command must not delay shutdown behind the request lock.
            if process.is_alive():
                try:
                    self._pipe.send({"kind": "close"})
                except (OSError, BrokenPipeError):
                    pass
                await asyncio.to_thread(process.join, 1.0)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 1.0)
            if process.is_alive():
                process.kill()
                await asyncio.to_thread(process.join, 1.0)
            if process.is_alive():
                raise GatewayError("Socket owner closure unconfirmed")
        if self._pipe:
            self._pipe.close()
        update_current({"transport_closed": True, "owner_stopped": True})
        return {
            "transport_closed": True,
            "owner_stopped": True,
            "owner_exitcode": process.exitcode if process else None,
        }
