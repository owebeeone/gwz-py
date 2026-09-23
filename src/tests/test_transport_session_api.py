from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from gwz import Client
from gwz.bridge import NativeCoreBridge
from gwz.errors import GwzBridgeError
from gwz.protocol.generated import FetchRequest
from test_bridge_transport import status_request


class FakeSession:
    def __init__(self) -> None:
        self.closes = 0
        self.cancelled: list[str] = []

    def close(self) -> tuple[int, bool]:
        self.closes += 1
        return (2, False)

    def cancel_operation(self, operation_id: str) -> tuple[int, bool]:
        if operation_id != "op_owned":
            error = ValueError("operation is not owned by this session")
            error.code = "InvalidRequest"  # type: ignore[attr-defined]
            raise error
        self.cancelled.append(operation_id)
        return (1, True)


class FakeModule:
    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []

    def TransportSession(self) -> FakeSession:
        session = FakeSession()
        self.sessions.append(session)
        return session


def test_close_is_awaitable_retains_cleanup_and_context_exit_uses_it() -> None:
    async def run() -> None:
        native = FakeModule()
        bridge = NativeCoreBridge(native=native)
        client = Client(bridge=bridge)
        async with client:
            pass
        first = await client.close()
        second = await client.close()
        assert first == second
        assert first is not None
        assert first.pending_local_work == 2
        assert first.peer_cleanup_confirmed is False
        assert len(native.sessions) == 1
        assert native.sessions[0].closes == 1

    asyncio.run(run())


def test_close_before_bridge_creation_is_terminal() -> None:
    async def run() -> None:
        client = Client()
        assert await client.close() is None
        with pytest.raises(GwzBridgeError, match="closed") as raised:
            await client.status()
        assert raised.value.code == "InvalidRequest"
        assert await client.close() is None

    asyncio.run(run())


def test_cancel_uses_public_operation_id_and_rejects_foreign_identity() -> None:
    async def run() -> None:
        native = FakeModule()
        client = Client(bridge=NativeCoreBridge(native=native))
        result = await client.cancel_operation("op_owned")
        assert result.pending_local_work == 1
        assert result.peer_cleanup_confirmed is True
        with pytest.raises(GwzBridgeError, match="not owned") as raised:
            await client.cancel_operation("op_foreign")
        assert raised.value.code == "InvalidRequest"
        assert native.sessions[0].cancelled == ["op_owned"]

    asyncio.run(run())


class BlockingSession(FakeSession):
    def __init__(self, entered: Event, release: Event) -> None:
        super().__init__()
        self.entered = entered
        self.release = release
        self.calls = 0

    def call(self, _method: str, _request: str, _response: str, _payload: bytes) -> bytes:
        self.calls += 1
        self.entered.set()
        self.release.wait(timeout=2)
        return b""


def test_cancelled_python_lock_waiter_never_enters_native_call() -> None:
    async def run() -> None:
        entered = Event()
        release = Event()
        session = BlockingSession(entered, release)

        class Module:
            def TransportSession(self) -> BlockingSession:
                return session

        bridge = NativeCoreBridge(native=Module())
        request = FetchRequest(meta=status_request().meta)
        first = asyncio.create_task(
            bridge.call("fetch", "FetchRequest", "FetchResponse", request)
        )
        await asyncio.to_thread(entered.wait, 1)
        second = asyncio.create_task(
            bridge.call("fetch", "FetchRequest", "FetchResponse", request)
        )
        await asyncio.sleep(0)
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        release.set()
        with pytest.raises(Exception):
            await first
        assert session.calls == 1
        assert session.cancelled == []

    asyncio.run(run())


def test_cancel_before_default_executor_starts_worker_reaches_reserved_operation() -> None:
    async def run() -> None:
        blocked = Event()
        release = Event()

        class ReservedSession(FakeSession):
            def __init__(self) -> None:
                super().__init__()
                self.reserved: str | None = None
                self.calls = 0

            def reserve_operation(self, operation_id: str) -> None:
                self.reserved = operation_id

            def cancel_operation(self, operation_id: str) -> tuple[int, bool]:
                assert operation_id == self.reserved
                self.cancelled.append(operation_id)
                return (0, True)

            def call(self, *_args: object) -> bytes:
                self.calls += 1
                assert self.cancelled == [self.reserved]
                return b""

        session = ReservedSession()

        class Module:
            def TransportSession(self) -> ReservedSession:
                return session

        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as worker_pool:
            loop.set_default_executor(worker_pool)

            def occupy_worker() -> None:
                blocked.set()
                release.wait(timeout=2)

            blocker = loop.run_in_executor(None, occupy_worker)
            await asyncio.sleep(0)
            assert blocked.is_set()
            request = FetchRequest(meta=status_request().meta)
            task = asyncio.create_task(
                NativeCoreBridge(native=Module()).call("fetch", "FetchRequest", "FetchResponse", request)
            )
            await asyncio.sleep(0)
            assert session.reserved == "op_req_transport"
            task.cancel()
            await asyncio.sleep(0.02)
            assert session.cancelled == ["op_req_transport"]
            release.set()
            await blocker
            with pytest.raises(asyncio.CancelledError):
                await task
            assert session.calls == 1

    asyncio.run(run())


def test_repeated_task_cancellation_waits_for_native_cleanup() -> None:
    async def run() -> None:
        entered = Event()
        cleanup_entered = Event()
        release = Event()

        class SlowCleanupSession(FakeSession):
            def reserve_operation(self, _operation_id: str) -> None:
                pass

            def call(self, *_args: object) -> bytes:
                entered.set()
                release.wait(timeout=2)
                return b""

            def cancel_operation(self, operation_id: str) -> tuple[int, bool]:
                self.cancelled.append(operation_id)
                cleanup_entered.set()
                release.wait(timeout=2)
                return (0, True)

        session = SlowCleanupSession()

        class Module:
            def TransportSession(self) -> SlowCleanupSession:
                return session

        bridge = NativeCoreBridge(native=Module())
        request = FetchRequest(meta=status_request().meta)
        task = asyncio.create_task(bridge.call("fetch", "FetchRequest", "FetchResponse", request))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        assert await asyncio.to_thread(cleanup_entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert session.cancelled == ["op_req_transport"]

    asyncio.run(run())
