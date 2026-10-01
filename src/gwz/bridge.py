from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from functools import partial
from threading import Lock, Thread
from typing import Any, NamedTuple, Protocol, TypeAlias

from .errors import GwzBridgeError, GwzCoreLoadError, GwzProtocolError
from .protocol.codec import decode_message, encode_message, event_message_name, result_message_name

NativeBytePayload: TypeAlias = bytes | bytearray | memoryview
_EVENT_WAIT_TIMEOUT_MS = 30_000
_DIFF_OUTPUT_RECORD_MESSAGE = "DiffOutputRecord"
_LOG_OUTPUT_RECORD_MESSAGE = "LogOutputRecord"


@dataclass(frozen=True, slots=True)
class TransportCleanup:
    """Final native transport cleanup facts: one operation's, from
    ``cancel_operation``, or a ``Client``'s, from ``close``."""

    pending_local_work: int
    peer_cleanup_confirmed: bool


async def _await_completion(task: asyncio.Task[Any]) -> Any:
    """Keep waiting for a native worker through repeated caller cancellation."""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


async def _on_own_thread(name: str, function: Callable[[], Any]) -> Any:
    """Run a bounded native wait on a thread of its own. A network call that
    waits for a slot holds its default-executor thread (gwz-py
    dev-docs/GwzPyPerOperationTransportDesign.md §2.4), so close and cancel,
    which release such calls, must not queue behind them there."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()

    def settle(outcome: Callable[[], None]) -> None:
        if not future.done():
            outcome()

    def run() -> None:
        try:
            outcome = partial(future.set_result, function())
        except BaseException as exc:  # noqa: BLE001 - delivered to the awaiting task
            outcome = partial(future.set_exception, exc)
        try:
            loop.call_soon_threadsafe(settle, outcome)
        except RuntimeError:
            pass  # The loop has closed; nobody awaits the outcome.

    Thread(target=run, name=name, daemon=True).start()
    return await future


class DiffLogRead(NamedTuple):
    """One `diff.output` read answer: decoded records, the always-present resume
    cursor (taut-shape D8), and the delivery state token."""

    records: list[Any]
    next_cursor: int
    state: str


class LogOutputRead(NamedTuple):
    """One bounded commit-log output read with an opaque resume cursor."""

    records: list[Any]
    next_cursor: int
    state: str


class CoreBridge(Protocol):
    async def call(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request: Any,
    ) -> Any:
        """Run one GWZ core operation and return a generated response object."""

    async def submit(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request: Any,
    ) -> Any:
        """Submit one GWZ core operation and return its accepted response."""

    def subscribe_events(self, operation_id: str) -> AsyncIterator[Any]:
        """Yield generated OperationEvent objects for a submitted operation."""

    async def operation_result(self, operation_id: str) -> Any:
        """Return the generated OperationResult for a submitted operation."""

    async def merge_operation_response(self, operation_id: str) -> Any:
        """Return the retained successful MergeResponse for an operation."""

    async def diff_log_read(
        self,
        log_id: str,
        stream_id: str,
        *,
        cursor: int | None = None,
        max_records: int | None = None,
        max_bytes: int | None = None,
        timeout_ms: int | None = None,
    ) -> DiffLogRead:
        """Read decoded DiffOutputRecords from a `diff.output` log by log_id."""

    async def diff_log_end_stream(self, log_id: str, stream_id: str) -> None:
        """End a `diff.output` reader stream (cancellation / cleanup)."""

    async def log_output_read(
        self,
        log_id: str,
        *,
        cursor: int | None = None,
        max_records: int | None = None,
    ) -> LogOutputRead:
        """Read one bounded batch of commit-log output records."""

    async def log_output_release(self, log_id: str) -> None:
        """Idempotently release a commit-log output and its temporary spool."""


class NativeClientHost(Protocol):
    """One ``Client``'s native host: the native entries, and the limit,
    cancellation and cleanup of its network operations."""

    def call(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request_bytes: bytes,
    ) -> NativeBytePayload:
        """Run one native gwz-core operation and return encoded response bytes."""

    def submit(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request_bytes: bytes,
    ) -> NativeBytePayload:
        """Submit one native gwz-core operation and return encoded accepted response bytes."""

    def cancel_operation(self, operation_id: str) -> tuple[int, bool]:
        """Cancel one network operation; its cleanup report."""

    def close(self) -> tuple[int, bool]:
        """Cancel and join the host's network operations; its cleanup report."""


class NativeModule(Protocol):
    def ClientHost(self) -> NativeClientHost:  # noqa: N802 - the native class
        """Create one ``Client``'s native host."""

    def subscribe_events(self, operation_id: str) -> Iterable[NativeBytePayload]:
        """Return encoded OperationEvent records for a submitted operation."""

    def wait_events(
        self,
        operation_id: str,
        after_sequence: int,
        timeout_ms: int,
    ) -> tuple[Iterable[NativeBytePayload], bool]:
        """Block until new encoded OperationEvent records or operation completion."""

    def operation_result(self, operation_id: str) -> NativeBytePayload:
        """Return encoded OperationResult bytes for a submitted operation."""

    def try_operation_result(self, operation_id: str) -> NativeBytePayload | None:
        """Return encoded OperationResult bytes if a submitted operation is complete."""

    def merge_operation_response(self, operation_id: str) -> NativeBytePayload:
        """Return encoded retained MergeResponse bytes for a successful merge."""

    def diff_log_read(
        self,
        log_id: str,
        stream_id: str,
        cursor: int | None,
        max_records: int | None,
        max_bytes: int | None,
        timeout_ms: int | None,
    ) -> tuple[Iterable[NativeBytePayload], int, str]:
        """Read encoded DiffOutputRecord payloads, the resume cursor, and state."""

    def diff_log_end_stream(self, log_id: str, stream_id: str) -> None:
        """End a `diff.output` reader stream."""

    def log_output_read(
        self,
        log_id: str,
        cursor: int | None,
        max_records: int | None,
    ) -> tuple[Iterable[NativeBytePayload], int, str]:
        """Read encoded LogOutputRecord payloads, cursor, and state."""

    def log_output_release(self, log_id: str) -> None:
        """Release a commit-log output spool."""


class NativeCoreBridge:
    """The ``Client``'s bridge to the PyO3 extension that embeds gwz-core.

    Each bridge creates one native ``ClientHost`` (gwz-py
    dev-docs/GwzPyPerOperationTransportDesign.md §2.4). Every ``call`` and
    ``submit`` goes through it, and gwz-core's transport scope, which gwz-cli
    shares, decides which are network operations: at most 8 of those run at
    once for this ``Client``, a further one waits, and each can be cancelled.
    """

    def __init__(self, native: NativeModule | None = None) -> None:
        if native is None:
            try:
                from . import _gwz_core
            except ImportError as exc:
                raise GwzCoreLoadError(
                    "gwz._gwz_core is not installed yet; pass a custom bridge for tests "
                    "or build the native gwz-core extension"
                ) from exc
            native = _gwz_core
        self._native = native
        self._host = native.ClientHost()
        self._closed = False
        self._close_result: TransportCleanup | None = None
        self._close_guard = Lock()

    def _ensure_open(self) -> None:
        if self._closed:
            raise GwzBridgeError("client is closed", code="InvalidRequest")

    async def close(self) -> TransportCleanup:
        """Cancel this client's network operations and join them, up to the
        cleanup bound (design §2.6). The report counts what was cancelled and
        leaves cleanup unconfirmed for any operation that has not finished;
        every later close returns it."""
        with self._close_guard:
            self._closed = True
        worker = asyncio.create_task(_on_own_thread("gwz-py-close", self._host.close))
        try:
            report = await asyncio.shield(worker)
        except asyncio.CancelledError:
            # The host's close is bounded: keep its report, then honour the
            # caller's cancellation.
            try:
                self._keep_close_report(await _await_completion(worker))
            except Exception:
                pass
            raise
        except Exception as exc:
            raise _native_bridge_error("native client close failed", exc) from exc
        return self._keep_close_report(report)

    def _keep_close_report(self, report: tuple[int, bool]) -> TransportCleanup:
        with self._close_guard:
            if self._close_result is None:
                self._close_result = TransportCleanup(*report)
            return self._close_result

    @property
    def close_report(self) -> TransportCleanup | None:
        return self._close_result

    async def cancel_operation(self, operation_id: str) -> TransportCleanup:
        """Cancel one of this client's network operations (design §2.5). A
        waiting one is refused with ``Cancelled``; a running one is cancelled,
        and its cleanup report returns once it ends or the cleanup bound
        passes. A wrong, foreign, completed or non-network operation fails
        without cancelling anything."""
        try:
            report = await _on_own_thread(
                "gwz-py-cancel", lambda: self._host.cancel_operation(operation_id)
            )
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise _native_bridge_error(f"native cancel failed for {operation_id}", exc) from exc
        return TransportCleanup(*report)

    async def release_operation(self, operation_id: str) -> None:
        """Unavailable until the session host serves this bridge (contract §10)."""
        raise GwzBridgeError("native operation release is unavailable", code="UnsupportedOperation")

    async def _run_native(
        self,
        native_call: Any,
        method: str,
        request_message: str,
        response_message: str,
        request_bytes: bytes,
    ) -> NativeBytePayload:
        worker = asyncio.create_task(
            asyncio.to_thread(native_call, method, request_message, response_message, request_bytes)
        )
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            try:
                await _await_completion(worker)
            except Exception:
                pass
            raise

    async def call(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request: Any,
    ) -> Any:
        self._ensure_open()
        request_bytes = encode_message(request_message, request)
        try:
            response_bytes = await self._run_native(
                self._host.call, method, request_message, response_message, request_bytes,
            )
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise _native_bridge_error(f"native bridge call failed for {method}", exc) from exc
        return decode_message(response_message, _bytes(response_bytes, response_message))

    async def submit(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request: Any,
    ) -> Any:
        self._ensure_open()
        request_bytes = encode_message(request_message, request)
        try:
            response_bytes = await self._run_native(
                self._host.submit, method, request_message, response_message, request_bytes,
            )
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise _native_bridge_error(f"native bridge submit failed for {method}", exc) from exc
        return decode_message(response_message, _bytes(response_bytes, response_message))

    def subscribe_events(self, operation_id: str) -> AsyncIterator[Any]:
        async def _events() -> AsyncIterator[Any]:
            message_name = event_message_name()
            if not hasattr(self._native, "wait_events"):
                for item in await self._event_bytes(operation_id):
                    yield decode_message(message_name, _bytes(item, message_name))
                return

            next_sequence = 0
            while True:
                event_bytes, complete = await self._wait_event_bytes(operation_id, next_sequence)
                for item in event_bytes:
                    event = decode_message(message_name, _bytes(item, message_name))
                    if event.sequence < next_sequence:
                        continue
                    next_sequence = event.sequence + 1
                    yield event
                if complete:
                    return

        return _events()

    async def _event_bytes(self, operation_id: str) -> list[NativeBytePayload]:
        try:
            return await asyncio.to_thread(lambda: list(self._native.subscribe_events(operation_id)))
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise _native_bridge_error(f"native event subscription failed for {operation_id}", exc) from exc

    async def _wait_event_bytes(
        self,
        operation_id: str,
        after_sequence: int,
    ) -> tuple[list[NativeBytePayload], bool]:
        try:
            wait_events = self._native.wait_events
            event_bytes, complete = await asyncio.to_thread(
                wait_events,
                operation_id,
                after_sequence,
                _EVENT_WAIT_TIMEOUT_MS,
            )
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise _native_bridge_error(f"native event wait failed for {operation_id}", exc) from exc
        return list(event_bytes), bool(complete)

    async def operation_result(self, operation_id: str) -> Any:
        try:
            result_bytes = await asyncio.to_thread(self._native.operation_result, operation_id)
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise _native_bridge_error(f"native operation result lookup failed for {operation_id}", exc) from exc
        return decode_message(result_message_name(), _bytes(result_bytes, result_message_name()))

    async def merge_operation_response(self, operation_id: str) -> Any:
        message_name = "MergeResponse"
        try:
            response_bytes = await asyncio.to_thread(
                self._native.merge_operation_response,
                operation_id,
            )
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise _native_bridge_error(f"native merge response lookup failed for {operation_id}", exc) from exc
        return decode_message(message_name, _bytes(response_bytes, message_name))

    async def diff_log_read(
        self,
        log_id: str,
        stream_id: str,
        *,
        cursor: int | None = None,
        max_records: int | None = None,
        max_bytes: int | None = None,
        timeout_ms: int | None = None,
    ) -> DiffLogRead:
        try:
            records, next_cursor, state = await asyncio.to_thread(
                self._native.diff_log_read,
                log_id,
                stream_id,
                cursor,
                max_records,
                max_bytes,
                timeout_ms,
            )
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise GwzBridgeError(
                f"native diff.output read failed for {log_id}/{stream_id}: {exc}"
            ) from exc
        decoded = [
            decode_message(
                _DIFF_OUTPUT_RECORD_MESSAGE,
                _bytes(item, _DIFF_OUTPUT_RECORD_MESSAGE),
            )
            for item in records
        ]
        return DiffLogRead(records=decoded, next_cursor=int(next_cursor), state=str(state))

    async def diff_log_end_stream(self, log_id: str, stream_id: str) -> None:
        try:
            await asyncio.to_thread(self._native.diff_log_end_stream, log_id, stream_id)
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise GwzBridgeError(
                f"native diff.output end_stream failed for {log_id}/{stream_id}: {exc}"
            ) from exc

    async def log_output_read(
        self,
        log_id: str,
        *,
        cursor: int | None = None,
        max_records: int | None = None,
    ) -> LogOutputRead:
        try:
            records, next_cursor, state = await asyncio.to_thread(
                self._native.log_output_read,
                log_id,
                cursor,
                max_records,
            )
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise _native_bridge_error(
                f"native log output read failed for {log_id}", exc
            ) from exc
        decoded = [
            decode_message(
                _LOG_OUTPUT_RECORD_MESSAGE,
                _bytes(item, _LOG_OUTPUT_RECORD_MESSAGE),
            )
            for item in records
        ]
        return LogOutputRead(
            records=decoded,
            next_cursor=int(next_cursor),
            state=str(state),
        )

    async def log_output_release(self, log_id: str) -> None:
        try:
            await asyncio.to_thread(self._native.log_output_release, log_id)
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise _native_bridge_error(
                f"native log output release failed for {log_id}", exc
            ) from exc


def _bytes(value: NativeBytePayload, message_name: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    raise GwzProtocolError(
        f"native bridge returned {type(value).__name__} for {message_name}; expected bytes-like payload"
    )


def _native_bridge_error(prefix: str, error: BaseException) -> GwzBridgeError:
    meta_bytes = getattr(error, "response_meta_cbor", None)
    response_meta = decode_message("ResponseMeta", _bytes(meta_bytes, "ResponseMeta")) if meta_bytes is not None else None
    return GwzBridgeError(
        f"{prefix}: {error}",
        code=getattr(error, "code", None),
        member_id=getattr(error, "member_id", None),
        member_path=getattr(error, "member_path", None),
        target_kind=getattr(error, "target_kind", None),
        detail=getattr(error, "detail", None),
        machine_message=getattr(error, "machine_message", None),
        record_context=getattr(error, "record_context", None),
        response_meta=response_meta,
        operation_id=getattr(error, "operation_id", None),
    )
