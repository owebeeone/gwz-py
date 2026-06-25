from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Any, Protocol, TypeAlias

from .errors import GwzBridgeError, GwzCoreLoadError, GwzProtocolError
from .protocol.codec import decode_message, encode_message, event_message_name, result_message_name

NativeBytePayload: TypeAlias = bytes | bytearray | memoryview
_EVENT_WAIT_TIMEOUT_MS = 30_000


class CoreBridge(Protocol):
    async def call(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request: Any,
    ) -> Any:
        """Run one GWZ core operation and return a generated response object."""

    def subscribe_events(self, operation_id: str) -> AsyncIterator[Any]:
        """Yield generated OperationEvent objects for a submitted operation."""

    async def operation_result(self, operation_id: str) -> Any:
        """Return the generated OperationResult for a submitted operation."""


class NativeModule(Protocol):
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


class NativeCoreBridge:
    """Loader for the future PyO3 extension that embeds gwz-core."""

    def __init__(self, native: NativeModule | None = None) -> None:
        if native is not None:
            self._native = native
            return
        try:
            from . import _gwz_core
        except ImportError as exc:
            raise GwzCoreLoadError(
                "gwz._gwz_core is not installed yet; pass a custom bridge for tests "
                "or build the native gwz-core extension"
            ) from exc
        self._native = _gwz_core

    async def call(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request: Any,
    ) -> Any:
        request_bytes = encode_message(request_message, request)
        try:
            response_bytes = await asyncio.to_thread(
                self._native.call,
                method,
                request_message,
                response_message,
                request_bytes,
            )
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise GwzBridgeError(f"native bridge call failed for {method}: {exc}") from exc
        return decode_message(response_message, _bytes(response_bytes, response_message))

    async def submit(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request: Any,
    ) -> Any:
        request_bytes = encode_message(request_message, request)
        try:
            submit = getattr(self._native, "submit")
            response_bytes = await asyncio.to_thread(
                submit,
                method,
                request_message,
                response_message,
                request_bytes,
            )
        except AttributeError:
            return await self.call(method, request_message, response_message, request)
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise GwzBridgeError(f"native bridge submit failed for {method}: {exc}") from exc
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
            raise GwzBridgeError(
                f"native event subscription failed for {operation_id}: {exc}"
            ) from exc

    async def _wait_event_bytes(
        self,
        operation_id: str,
        after_sequence: int,
    ) -> tuple[list[NativeBytePayload], bool]:
        try:
            wait_events = getattr(self._native, "wait_events")
            event_bytes, complete = await asyncio.to_thread(
                wait_events,
                operation_id,
                after_sequence,
                _EVENT_WAIT_TIMEOUT_MS,
            )
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise GwzBridgeError(
                f"native event wait failed for {operation_id}: {exc}"
            ) from exc
        return list(event_bytes), bool(complete)

    async def operation_result(self, operation_id: str) -> Any:
        try:
            result_bytes = await asyncio.to_thread(self._native.operation_result, operation_id)
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise GwzBridgeError(
                f"native operation result lookup failed for {operation_id}: {exc}"
            ) from exc
        return decode_message(result_message_name(), _bytes(result_bytes, result_message_name()))


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
