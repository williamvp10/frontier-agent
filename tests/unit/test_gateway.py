import asyncio
import time
from datetime import timedelta
from uuid import uuid4

import pytest

from app.desktop.gateway import GatewayError, ProcessGateway
from app.domain.contracts import utcnow

pytestmark = pytest.mark.unit


class FakeTransport:
    def __init__(self, *args):
        pass

    async def open(self):
        pass

    async def capture(self):
        return b"fake", 1280, 800

    async def action(self, args):
        pass

    async def close(self):
        pass


class BlockedTransport(FakeTransport):
    async def capture(self):
        # Intentionally block the owner's event loop; asyncio cancellation cannot help.
        time.sleep(15)
        return b"fake", 1280, 800


class FailedCaptureTransport(FakeTransport):
    async def capture(self):
        raise TimeoutError("PRIVATE_PROVIDER_ERROR_CANARY")


async def test_capture_failure_has_static_diagnostic_without_exception_text():
    gateway = ProcessGateway(uuid4(), 1, utcnow() + timedelta(seconds=30),
                             transport_factory=FailedCaptureTransport)
    await gateway.open("wss://synthetic.example", "not-a-real-password")
    try:
        with pytest.raises(GatewayError) as error:
            await gateway.request("observe")
        assert error.value.diagnostic_code == "capture_timeout"
        assert "PRIVATE_PROVIDER_ERROR_CANARY" not in str(error.value)
    finally:
        evidence = await gateway.close_and_confirm()
    assert evidence["owner_stopped"] and evidence["transport_closed"]


async def test_gateway_serialized_and_closed_before_reuse():
    gateway = ProcessGateway(
        uuid4(), 1, utcnow() + timedelta(seconds=30), transport_factory=FakeTransport
    )
    await gateway.open("wss://synthetic.example", "not-a-real-password")
    result = await gateway.request("observe")
    assert result["width"] == 1280
    evidence = await gateway.close_and_confirm()
    assert evidence["transport_closed"] and evidence["owner_stopped"]
    with pytest.raises(GatewayError):
        await gateway.request("observe")


async def test_hard_deadline_kills_socket_owner_with_blocked_event_loop():
    gateway = ProcessGateway(
        uuid4(), 1, utcnow() + timedelta(seconds=4), transport_factory=BlockedTransport
    )
    await gateway.open("wss://synthetic.example", "not-a-real-password")
    with pytest.raises(GatewayError):
        await gateway.request("observe")
    evidence = await gateway.close_and_confirm()
    assert evidence["owner_stopped"] and evidence["owner_exitcode"] == 90


async def test_parent_close_kills_unresponsive_owner():
    gateway = ProcessGateway(
        uuid4(), 1, utcnow() + timedelta(seconds=30), transport_factory=BlockedTransport
    )
    await gateway.open("wss://synthetic.example", "not-a-real-password")
    pending = asyncio.create_task(gateway.request("observe"))
    await asyncio.sleep(0.2)
    evidence = await gateway.close_and_confirm()
    assert evidence["owner_stopped"]
    result = await asyncio.gather(pending, return_exceptions=True)
    assert isinstance(result[0], Exception)


class OneBadFrameTransport(FakeTransport):
    def __init__(self, *args):
        self.captures = 0
        self.actions = 0

    async def action(self, args):
        self.actions += 1

    async def capture(self):
        self.captures += 1
        if self.captures == 1:
            raise ValueError("PRIVATE_FRAME_CANARY")
        return f"fresh-after-reconnect:{self.actions}".encode(), 1280, 800


async def test_capture_protocol_reconnect_returns_new_evidence_without_replaying_action():
    gateway = ProcessGateway(uuid4(), 1, utcnow() + timedelta(seconds=40),
                             transport_factory=OneBadFrameTransport)
    await gateway.open("wss://synthetic.example", "not-a-real-password")
    try:
        await gateway.request("execute", args={"kind": "click", "x": 5, "y": 5, "hold_ms": 180})
        result = await gateway.request("observe")
        assert result["capture_reconnected"] is True
        assert result["image"] == b"fresh-after-reconnect:1"
        assert (await gateway.request("observe"))["capture_reconnected"] is False
    finally:
        evidence = await gateway.close_and_confirm()
    assert evidence["owner_stopped"] and evidence["transport_closed"]
