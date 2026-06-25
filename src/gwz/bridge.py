from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import Any, Protocol, TypeAlias

from .errors import GwzBridgeError, GwzCoreLoadError, GwzProtocolError
from .protocol.codec import decode_message, encode_message, event_message_name, result_message_name

NativeBytePayload: TypeAlias = bytes | bytearray | memoryview


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

    def subscribe_events(self, operation_id: str) -> Iterable[NativeBytePayload]:
        """Return encoded OperationEvent records for a submitted operation."""

    def operation_result(self, operation_id: str) -> NativeBytePayload:
        """Return encoded OperationResult bytes for a submitted operation."""


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
            raise GwzBridgeError(f"native bridge call failed for {method}") from exc
        return decode_message(response_message, _bytes(response_bytes, response_message))

    def subscribe_events(self, operation_id: str) -> AsyncIterator[Any]:
        async def _events() -> AsyncIterator[Any]:
            try:
                event_bytes = await asyncio.to_thread(
                    lambda: list(self._native.subscribe_events(operation_id))
                )
            except GwzBridgeError:
                raise
            except Exception as exc:
                raise GwzBridgeError(f"native event subscription failed for {operation_id}") from exc
            message_name = event_message_name()
            for item in event_bytes:
                yield decode_message(message_name, _bytes(item, message_name))

        return _events()

    async def operation_result(self, operation_id: str) -> Any:
        try:
            result_bytes = await asyncio.to_thread(self._native.operation_result, operation_id)
        except GwzBridgeError:
            raise
        except Exception as exc:
            raise GwzBridgeError(f"native operation result lookup failed for {operation_id}") from exc
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
