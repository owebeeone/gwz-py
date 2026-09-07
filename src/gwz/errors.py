from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class GwzError(Exception):
    """Base class for gwz-py errors."""


class GwzProtocolError(GwzError):
    """Raised when protocol records cannot be interpreted safely."""


class GwzBridgeError(GwzError):
    """Raised when the Python/Rust bridge fails outside normal GWZ operation handling."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        member_id: str | None = None,
        member_path: str | None = None,
        target_kind: str | None = None,
        detail: str | None = None,
        machine_message: str | None = None,
        record_context: Any | None = None,
        response_meta: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.member_id = member_id
        self.member_path = member_path
        self.target_kind = target_kind
        self.detail = detail
        self.machine_message = machine_message
        self.record_context = record_context
        self.response_meta = response_meta


class GwzCoreLoadError(GwzBridgeError):
    """Raised when the native gwz-core extension cannot be imported or initialized."""


@dataclass(slots=True)
class GwzOperationError(GwzError):
    """Raised when a GWZ response envelope reports an unsuccessful operation status."""

    message: str
    response: Any | None = None
    aggregate_status: Any | None = None
    operation_id: str | None = None
    request_id: str | None = None
    member_errors: list[Any] = field(default_factory=list)

    @property
    def response_meta(self) -> Any | None:
        from .protocol.generated import OperationResult, ResponseMeta

        meta = getattr(getattr(self.response, "response", None), "meta", None)
        if meta is not None and getattr(meta, "transport", None):
            return meta
        if isinstance(self.response, OperationResult) and self.response.transport:
            # The v0 stream result carries the same evidence outside an envelope.
            result = self.response
            return ResponseMeta(
                request_id=result.request_id, schema_version="gwz.protocol/v0",
                action=result.action, aggregate_status=result.aggregate_status,
                operation_id=result.operation_id, message=None,
                attribution=result.attribution, transport=result.transport,
            )
        return None

    def __str__(self) -> str:
        return self.message
