from __future__ import annotations

import asyncio
from threading import Event

import pytest

from gwz import Client, TransportCleanup
from gwz.bridge import NativeCoreBridge
from gwz.errors import GwzBridgeError
from gwz.protocol.generated import FetchRequest
from test_bridge_transport import status_request


def test_legacy_module_keeps_its_network_call_serialization() -> None:
    entered = Event()
    release = Event()

    class Module:
        def __init__(self) -> None:
            self.calls = 0

        def call(self, *_args: object) -> bytes:
            self.calls += 1
            entered.set()
            release.wait(timeout=2)
            return b""

    async def run() -> None:
        native = Module()
        bridge = NativeCoreBridge(native=native)
        request = FetchRequest(meta=status_request().meta)
        a = asyncio.create_task(bridge.call("fetch", "FetchRequest", "FetchResponse", request))
        assert await asyncio.to_thread(entered.wait, 1)
        b = asyncio.create_task(bridge.call("fetch", "FetchRequest", "FetchResponse", request))
        await asyncio.sleep(0.02)
        assert native.calls == 1
        release.set()
        await asyncio.gather(a, b, return_exceptions=True)

    asyncio.run(run())


def test_close_is_awaitable_retains_its_report_and_context_exit_uses_it() -> None:
    async def run() -> None:
        client = Client(bridge=NativeCoreBridge(native=object()))
        async with client:
            pass
        assert client.close_report == TransportCleanup(0, False)
        first = await client.close()
        second = await client.close()
        assert first == second == TransportCleanup(0, False)

    asyncio.run(run())


def test_close_before_bridge_creation_is_terminal() -> None:
    async def run() -> None:
        client = Client()
        assert await client.close() is None
        assert client.close_report is None
        with pytest.raises(GwzBridgeError, match="closed") as raised:
            await client.status()
        assert raised.value.code == "InvalidRequest"
        assert await client.close() is None

    asyncio.run(run())


def test_cancel_and_release_are_unavailable_until_the_session_host_serves_the_bridge() -> None:
    async def run() -> None:
        client = Client(bridge=NativeCoreBridge(native=object()))
        with pytest.raises(GwzBridgeError) as cancelled:
            await client.cancel_operation("op_any")
        assert cancelled.value.code == "UnsupportedOperation"
        with pytest.raises(GwzBridgeError) as released:
            await client.release_operation("op_any")
        assert released.value.code == "UnsupportedOperation"

    asyncio.run(run())
