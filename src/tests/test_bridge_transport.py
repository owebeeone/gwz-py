from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gwz.bridge import NativeCoreBridge
from gwz.errors import GwzBridgeError, GwzProtocolError
from gwz.protocol.codec import decode_message, encode_message
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    EventKind,
    OperationEvent,
    OperationResult,
    RequestMeta,
    ResponseEnvelope,
    ResponseMeta,
    Severity,
    StatusMode,
    StatusRequest,
    StatusResponse,
)


def status_request() -> StatusRequest:
    return StatusRequest(
        meta=RequestMeta(
            request_id="req_transport",
            schema_version="gwz.protocol/v0",
            workspace=None,
            selection=None,
            policy=None,
            dry_run=None,
            attribution=None,
        ),
        mode=StatusMode.summary,
        include_file_changes=None,
        include_branch_summary=None,
        path_style=None,
    )


def status_response() -> StatusResponse:
    return StatusResponse(
        response=ResponseEnvelope(
            meta=ResponseMeta(
                request_id="req_transport",
                schema_version="gwz.protocol/v0",
                action=ActionKind.status,
                aggregate_status=AggregateStatus.ok,
                operation_id="op_transport",
                message="ok",
                attribution=None,
            ),
            members=[],
            errors=[],
        ),
        workspace_git_status=None,
    )


def operation_event() -> OperationEvent:
    return OperationEvent(
        operation_id="op_transport",
        request_id="req_transport",
        sequence=1,
        timestamp_ms=1_700_000_000_000,
        kind=EventKind.operation_started,
        severity=Severity.info,
        member_id=None,
        member_path=None,
        message="started",
        member=None,
        error=None,
        attribution=None,
        progress=None,
    )


def operation_result() -> OperationResult:
    return OperationResult(
        operation_id="op_transport",
        request_id="req_transport",
        action=ActionKind.status,
        aggregate_status=AggregateStatus.ok,
        started_at_ms=1,
        finished_at_ms=2,
        members=[],
        errors=[],
        attribution=None,
    )


class FakeNative:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, bytes]] = []
        self.subscriptions: list[str] = []
        self.result_requests: list[str] = []

    def call(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request_bytes: bytes,
    ) -> bytes:
        self.calls.append((method, request_message, response_message, request_bytes))
        assert decode_message(request_message, request_bytes) == status_request()
        return encode_message(response_message, status_response())

    def subscribe_events(self, operation_id: str) -> list[bytes]:
        self.subscriptions.append(operation_id)
        return [encode_message("OperationEvent", operation_event())]

    def operation_result(self, operation_id: str) -> bytes:
        self.result_requests.append(operation_id)
        return encode_message("OperationResult", operation_result())


def test_native_bridge_encodes_request_bytes_and_decodes_response() -> None:
    native = FakeNative()
    bridge = NativeCoreBridge(native=native)

    response = asyncio.run(
        bridge.call("status", "StatusRequest", "StatusResponse", status_request())
    )

    assert response == status_response()
    method, request_message, response_message, request_bytes = native.calls[0]
    assert method == "status"
    assert request_message == "StatusRequest"
    assert response_message == "StatusResponse"
    assert isinstance(request_bytes, bytes)


def test_native_bridge_decodes_event_and_result_bytes() -> None:
    native = FakeNative()
    bridge = NativeCoreBridge(native=native)

    async def collect() -> list[OperationEvent]:
        return [event async for event in bridge.subscribe_events("op_transport")]

    assert asyncio.run(collect()) == [operation_event()]
    assert native.subscriptions == ["op_transport"]

    result = asyncio.run(bridge.operation_result("op_transport"))

    assert result == operation_result()
    assert native.result_requests == ["op_transport"]


def test_native_bridge_maps_native_failures_to_bridge_error() -> None:
    class FailingNative(FakeNative):
        def call(self, *args: Any) -> bytes:
            raise RuntimeError("native exploded")

    bridge = NativeCoreBridge(native=FailingNative())

    with pytest.raises(GwzBridgeError, match="native bridge call failed"):
        asyncio.run(bridge.call("status", "StatusRequest", "StatusResponse", status_request()))


def test_native_bridge_maps_malformed_response_bytes_to_protocol_error() -> None:
    class BadBytesNative(FakeNative):
        def call(self, *args: Any) -> bytes:
            return b"not-cbor"

    bridge = NativeCoreBridge(native=BadBytesNative())

    with pytest.raises(GwzProtocolError, match="failed to decode StatusResponse"):
        asyncio.run(bridge.call("status", "StatusRequest", "StatusResponse", status_request()))
