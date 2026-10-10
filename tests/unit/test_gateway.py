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
