from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from gwz import Client
from gwz.bridge import NativeCoreBridge
from gwz.errors import GwzBridgeError
from gwz.protocol.generated import ActionKind, AggregateStatus, FetchRequest, OperationResult
from gwz.protocol.codec import encode_message
from test_bridge_transport import status_request


def test_two_native_session_results_with_the_same_id_do_not_use_module_store() -> None:
    class Session:
        def __init__(self, request_id: str) -> None:
            self.request_id = request_id

        def operation_result(self, operation_id: str) -> bytes:
            return encode_message("OperationResult", OperationResult(
                operation_id=operation_id, request_id=self.request_id,
                action=ActionKind.fetch, aggregate_status=AggregateStatus.ok,
                started_at_ms=0, finished_at_ms=1, members=[], errors=[],
                attribution=None, transport=None,
            ))

    class Module:
        def __init__(self) -> None:
            self.next_id = 0

        def TransportSession(self) -> Session:
            self.next_id += 1
            return Session(f"request-{self.next_id}")

        def operation_result(self, _operation_id: str) -> bytes:
            raise AssertionError("network result escaped to process-global store")

    async def run() -> None:
        native = Module()
        a = NativeCoreBridge(native=native)
        b = NativeCoreBridge(native=native)
        a._network_operation_ids.add("op_shared")
        b._network_operation_ids.add("op_shared")
        assert (await a.operation_result("op_shared")).request_id == "request-1"
        assert (await b.operation_result("op_shared")).request_id == "request-2"

    asyncio.run(run())


def test_current_native_session_never_reads_legacy_or_foreign_results() -> None:
    class Session:
        def issued_operation(self, _operation_id: str) -> bool:
            return False

        def operation_result(self, _operation_id: str) -> bytes:
            failure = RuntimeError("operation is not owned by this session")
            failure.code = "InvalidRequest"  # type: ignore[attr-defined]
            raise failure

    class Module:
        def TransportSession(self) -> Session:
            return Session()

        def operation_result(self, _operation_id: str) -> bytes:
            raise AssertionError("native Client reached the legacy store")

    async def run() -> None:
        bridge = NativeCoreBridge(native=Module())
        with pytest.raises(GwzBridgeError) as failure:
            await bridge.operation_result("op_legacy")
        assert failure.value.code == "InvalidRequest"

    asyncio.run(run())


def test_native_bridge_does_not_serialize_independent_network_calls() -> None:
    entered = Event()
    release = Event()

    class Session:
        def __init__(self) -> None:
            self.calls = 0

        def call(self, *_args: object) -> bytes:
            self.calls += 1
            if self.calls == 2:
                entered.set()
            release.wait(timeout=2)
            return b""

    session = Session()

    class Module:
        def TransportSession(self) -> Session:
            return session

    async def run() -> None:
        bridge = NativeCoreBridge(native=Module())
        request = FetchRequest(meta=status_request().meta)
        a = asyncio.create_task(bridge.call("fetch", "FetchRequest", "FetchResponse", request))
        b = asyncio.create_task(bridge.call("fetch", "FetchRequest", "FetchResponse", request))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
        finally:
            release.set()
            await asyncio.gather(a, b, return_exceptions=True)

    asyncio.run(run())


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


def test_typed_capacity_refusal_reaches_python_without_message_parsing() -> None:
    class Session:
        def call(self, *_args: object) -> bytes:
            failure = RuntimeError("physical capacity unavailable")
            failure.code = "TransportCapacityConflict"  # type: ignore[attr-defined]
            raise failure

    class Module:
        def TransportSession(self) -> Session:
            return Session()

    async def run() -> None:
        bridge = NativeCoreBridge(native=Module())
        request = FetchRequest(meta=status_request().meta)
        with pytest.raises(GwzBridgeError) as raised:
            await bridge.call("fetch", "FetchRequest", "FetchResponse", request)
        assert raised.value.code == "TransportCapacityConflict"

    asyncio.run(run())


def test_public_identity_is_issued_once_before_native_work() -> None:
    class Session:
        def __init__(self) -> None:
            self.reservations = 0
            self.calls = 0

        def reserve_operation(self, request_id: str) -> str:
            self.reservations += 1
            assert request_id == "req_transport"
            return "public-1"

        def call(self, *_args: object) -> bytes:
            self.calls += 1
            return b""

    session = Session()

    class Module:
        def TransportSession(self) -> Session:
            return session

    async def run() -> None:
        bridge = NativeCoreBridge(native=Module())
        assert bridge.issue_operation("req_transport") == "public-1"
        assert session.calls == 0
        request = FetchRequest(meta=status_request().meta)
        with pytest.raises(Exception):
            await bridge.call("fetch", "FetchRequest", "FetchResponse", request)
        assert session.reservations == 1
        assert session.calls == 1

    asyncio.run(run())


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
        assert client.close_report is not None
        assert client.close_report.pending_local_work == 2
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
        assert client.close_report is None
        with pytest.raises(GwzBridgeError, match="closed") as raised:
            await client.status()
        assert raised.value.code == "InvalidRequest"
        assert await client.close() is None

    asyncio.run(run())


def test_repeated_close_waiter_cancellation_still_retains_one_native_cleanup() -> None:
    entered = Event()
    release = Event()

    class BlockingClose(FakeSession):
        def close(self) -> tuple[int, bool]:
            self.closes += 1
            entered.set()
            release.wait(timeout=3)
            return (3, True)

    class Module:
        def __init__(self) -> None:
            self.session = BlockingClose()

        def TransportSession(self) -> BlockingClose:
            return self.session

    async def run() -> None:
        native = Module()
        client = Client(bridge=NativeCoreBridge(native=native))
        closing = asyncio.create_task(client.close())
        assert await asyncio.to_thread(entered.wait, 1)
        closing.cancel()
        closing.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert client.close_report is not None
        assert client.close_report.pending_local_work == 3
        assert (await client.close()).peer_cleanup_confirmed is True
        assert native.session.closes == 1

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


def test_cancel_before_default_executor_starts_worker_reaches_reserved_operation() -> None:
    async def run() -> None:
        blocked = Event()
        release = Event()

        class ReservedSession(FakeSession):
            def __init__(self) -> None:
                super().__init__()
                self.reserved: str | None = None
                self.calls = 0

            def reserve_operation(self, request_id: str) -> None:
                self.reserved = f"op_{request_id}"

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
