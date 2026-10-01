"""The bridge on its per-``Client`` native host (gwz-py
dev-docs/GwzPyPerOperationTransportDesign.md §2.1, §2.4–§2.6), with fake hosts.

The native host's own behaviour, against the real extension, is in
``test_client_host.py``.
"""

from __future__ import annotations

import asyncio
from threading import Barrier, Event

import pytest

import gwz.bridge as bridge_module
from fake_host import FakeModule, ModuleHost
from gwz import Client, TransportCleanup
from gwz.bridge import NativeCoreBridge
from gwz.errors import GwzBridgeError
from gwz.protocol.generated import FetchRequest, TagOp, TagRequest
from test_bridge_transport import status_request


class RecordingHost(ModuleHost):
    """Records which entry each method reached, and answers nothing useful."""

    def __init__(self, module: object) -> None:
        super().__init__(module)
        self.entries: list[tuple[str, str]] = []

    def call(self, method: str, *_args: object) -> bytes:
        self.entries.append(("call", method))
        return b""

    def submit(self, method: str, *_args: object) -> bytes:
        self.entries.append(("submit", method))
        return b""


class RecordingModule(FakeModule):
    def __init__(self) -> None:
        self.hosts: list[RecordingHost] = []

    def ClientHost(self) -> RecordingHost:  # noqa: N802 - the native class's name
        host = RecordingHost(self)
        self.hosts.append(host)
        return host


def test_the_bridge_sends_every_request_through_its_host_and_has_no_network_set() -> None:
    """One predicate decides which requests are network operations: gwz-core's,
    in the native host. The bridge keeps no method set of its own to drift
    from it (design §2.1), and every call and submit reaches the host."""
    assert not hasattr(bridge_module, "_needs_transport")
    assert not hasattr(bridge_module, "_NETWORK_METHODS")
    module = RecordingModule()
    bridge = NativeCoreBridge(native=module)
    meta = status_request().meta
    requests = [
        ("fetch", "FetchRequest", FetchRequest(meta=meta)),
        ("tag", "TagRequest", TagRequest(meta=meta, op=TagOp.push, name=None, message=None,
                                         signed=None, remote="origin", all=None)),
        ("status", "StatusRequest", status_request()),
    ]

    async def run() -> None:
        for method, message, request in requests:
            for entry in (bridge.call, bridge.submit):
                with pytest.raises(Exception):
                    # The recording host answers empty bytes, which do not decode.
                    await entry(method, message, message.replace("Request", "Response"), request)

    asyncio.run(run())
    assert len(module.hosts) == 1
    assert module.hosts[0].entries == [
        (entry, method) for method, _, _ in requests for entry in ("call", "submit")
    ]


def test_network_calls_are_no_longer_serialized_per_event_loop() -> None:
    """The per-event-loop lock is gone: the limit belongs to the Client's
    native host, so two network calls reach it together."""
    # Two calls and this test meet at the barrier only if both calls are in
    # the host at once.
    both = Barrier(3, timeout=2)
    released = Event()

    class Module(FakeModule):
        def call(self, *_args: object) -> bytes:
            both.wait()
            released.wait(timeout=2)
            return b""

    async def run() -> None:
        bridge = NativeCoreBridge(native=Module())
        request = FetchRequest(meta=status_request().meta)
        calls = [
            asyncio.create_task(bridge.call("fetch", "FetchRequest", "FetchResponse", request))
            for _ in range(2)
        ]
        await asyncio.to_thread(both.wait)
        released.set()
        await asyncio.gather(*calls, return_exceptions=True)

    asyncio.run(run())


def test_close_returns_and_keeps_the_hosts_report() -> None:
    class ReportingHost(ModuleHost):
        def close(self) -> tuple[int, bool]:
            self.closes += 1
            return (2, False)

    class Module(FakeModule):
        def __init__(self) -> None:
            self.host = ReportingHost(self)

        def ClientHost(self) -> ReportingHost:  # noqa: N802
            return self.host

    async def run() -> None:
        module = Module()
        client = Client(bridge=NativeCoreBridge(native=module))
        async with client:
            pass
        assert client.close_report == TransportCleanup(2, False)
        first = await client.close()
        second = await client.close()
        assert first == second == TransportCleanup(2, False)
        assert module.host.closes == 3

    asyncio.run(run())


def test_a_cancelled_close_keeps_its_report() -> None:
    entered = Event()
    finish = Event()

    class SlowHost(ModuleHost):
        def close(self) -> tuple[int, bool]:
            entered.set()
            finish.wait(timeout=2)
            return (1, False)

    class Module(FakeModule):
        def ClientHost(self) -> SlowHost:  # noqa: N802
            return SlowHost(self)

    async def run() -> None:
        client = Client(bridge=NativeCoreBridge(native=Module()))
        _ = client.bridge
        closing = asyncio.create_task(client.close())
        assert await asyncio.to_thread(entered.wait, 2)
        closing.cancel()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert client.close_report == TransportCleanup(1, False)

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


def test_cancel_reaches_the_host_and_release_stays_unavailable() -> None:
    class CancellingHost(ModuleHost):
        def cancel_operation(self, operation_id: str) -> tuple[int, bool]:
            self.cancels.append(operation_id)
            return (0, False)

    class Module(FakeModule):
        def __init__(self) -> None:
            self.host = CancellingHost(self)

        def ClientHost(self) -> CancellingHost:  # noqa: N802
            return self.host

    async def run() -> None:
        module = Module()
        client = Client(bridge=NativeCoreBridge(native=module))
        assert await client.cancel_operation("op_any") == TransportCleanup(0, False)
        assert module.host.cancels == ["op_any"]
        with pytest.raises(GwzBridgeError) as released:
            await client.release_operation("op_any")
        assert released.value.code == "UnsupportedOperation"

    asyncio.run(run())


def test_a_refused_cancel_keeps_its_code() -> None:
    async def run() -> None:
        client = Client(bridge=NativeCoreBridge(native=FakeModule()))
        with pytest.raises(GwzBridgeError) as refused:
            await client.cancel_operation("op_unknown")
        assert refused.value.code == "InvalidRequest"

    asyncio.run(run())


def test_close_and_cancel_are_not_queued_behind_calls_that_fill_the_default_executor() -> None:
    """A call that waits for a slot holds its default-executor thread (design
    §2.4), so close and cancel, which must reach the host to release it, run
    on threads of their own."""
    released = Event()

    class BlockingHost(ModuleHost):
        def call(self, *_args: object) -> bytes:
            released.wait(timeout=5)
            return b""

        def cancel_operation(self, operation_id: str) -> tuple[int, bool]:
            return (0, False)

        def close(self) -> tuple[int, bool]:
            released.set()
            return (0, False)

    class Module(FakeModule):
        def ClientHost(self) -> BlockingHost:  # noqa: N802
            return BlockingHost(self)

    async def run() -> None:
        from concurrent.futures import ThreadPoolExecutor

        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        client = Client(bridge=NativeCoreBridge(native=Module()))
        request = FetchRequest(meta=status_request().meta)
        waiting = asyncio.create_task(
            client.bridge.call("fetch", "FetchRequest", "FetchResponse", request)
        )
        await asyncio.sleep(0.05)
        assert await asyncio.wait_for(client.cancel_operation("op_x"), 2) == TransportCleanup(0, False)
        assert await asyncio.wait_for(client.close(), 2) == TransportCleanup(0, False)
        await asyncio.gather(waiting, return_exceptions=True)

    asyncio.run(run())
