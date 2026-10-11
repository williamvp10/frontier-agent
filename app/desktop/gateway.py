"""Spawned socket owner: a wall-clock-independent hard stop survives a blocked model."""

import asyncio
import multiprocessing as mp
import os
import threading
import time
from datetime import datetime
from functools import partial
from uuid import UUID

from app.core.observability import observed, update_current
from app.desktop.transport import VNCTransport
from app.domain.contracts import utcnow


class GatewayError(RuntimeError):
    @property
    def diagnostic_code(self):
        code = str(self)
        return code if code in {"transport_error", "capture_timeout", "capture_eof",
            "capture_protocol_error", "capture_error", "action_timeout", "action_eof",
            "action_protocol_error", "action_error", "Gateway response timed out",
            "Gateway stopped", "Gateway closed", "Action deadline reached"} else "gateway_error"


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
        stage = "connect"
        capture_reconnects = 0
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
                    stage = "capture"
                    reconnected = False
                    try:
                        image, width, height = await transport.capture()
                    except ValueError:
                        if capture_reconnects or cutoff - time.monotonic() <= 24:
                            raise
                        # Same fenced owner and deadline. Close the old transport
                        # before reconnecting; only a screenshot is retried, never input.
                        capture_reconnects += 1
                        async with asyncio.timeout(10):
                            await transport.close()
                            await transport.open()
                            image, width, height = await transport.capture()
                        reconnected = True
                    if time.monotonic() >= cutoff:
                        return
                    pipe.send({"ok": True, "image": image, "width": width, "height": height,
                               "capture_reconnected": reconnected})
                elif command["kind"] == "execute":
                    stage = "action"
                    # Worst-case allowance covers input release; fail closed near deadline.
                    if cutoff - time.monotonic() <= 3.0 + 2 * len(command["args"].get("text_targets", ())):
                        pipe.send({"ok": False, "code": "deadline"})
                        continue
                    await transport.action(command["args"])
                    pipe.send({"ok": True})
                else:
                    pipe.send({"ok": False, "code": "unsupported_command"})
        except BaseException as exc:
            suffix = "timeout" if isinstance(exc, TimeoutError) else (
                "eof" if isinstance(exc, (EOFError, asyncio.IncompleteReadError)) else (
                    "protocol_error" if isinstance(exc, ValueError) else "error"))
            code = f"{stage}_{suffix}" if stage in {"capture", "action"} else "transport_error"
            try:
                pipe.send({"ok": False, "code": code})
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
    async def open(self, url: str, password: str, *, allow_private=False, text_input="keys"):
        if text_input not in {"keys", "clipboard_utf8", "clipboard_latin1"}:
            raise GatewayError("Unsupported text input mode")
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
                partial(VNCTransport, text_input=text_input) if self._factory is VNCTransport else self._factory,
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
                return await self._receive(22 if kind == "observe" else 12)
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
