from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Protocol

from .errors import GwzCoreLoadError


class CoreBridge(Protocol):
    async def call(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request: Any,
    ) -> Any:
        """Run one GWZ core operation and return a generated response object."""

    def stream(
        self,
        method: str,
        request_message: str,
        event_message: str,
        request: Any,
    ) -> AsyncIterator[Any]:
        """Run or subscribe to one operation and yield generated event objects."""


class NativeCoreBridge:
    """Loader for the future PyO3 extension that embeds gwz-core."""

    def __init__(self) -> None:
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
        return await asyncio.to_thread(
            self._native.call,
            method,
            request_message,
            response_message,
            request,
        )

    def stream(
        self,
        method: str,
        request_message: str,
        event_message: str,
        request: Any,
    ) -> AsyncIterator[Any]:
        async def _missing() -> AsyncIterator[Any]:
            raise GwzCoreLoadError(
                "gwz._gwz_core streaming bridge is not implemented in this scaffold"
            )
            yield None

        return _missing()
