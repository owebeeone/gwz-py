from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gwz import Client, MergeOperationHandle
from gwz.bridge import NativeCoreBridge, _EVENT_WAIT_TIMEOUT_MS
from gwz.errors import GwzBridgeError, GwzOperationError, GwzProtocolError
from gwz.protocol.codec import decode_message, encode_message
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    EventKind,
    GwzError,
    GwzErrorCode,
    MergeOperationState,
    MergeParticipantCounts,
    MergeResponse,
    OperationEvent,
    OperationResult,
    RequestMeta,
    ResponseEnvelope,
    ResponseMeta,
    Severity,
    StatusMode,
    StatusRequest,
    StatusResponse,
    TargetKind,
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
        target_kind=None,
        merge_state=None,
        merge_member=None,
        artifact_path=None,
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


def merge_response(
    aggregate_status: AggregateStatus = AggregateStatus.ok,
) -> MergeResponse:
    return MergeResponse(
        response=ResponseEnvelope(
            meta=ResponseMeta(
                request_id="req_transport",
                schema_version="gwz.protocol/v0",
                action=ActionKind.merge,
                aggregate_status=aggregate_status,
                operation_id="op_transport",
                message=None,
                attribution=None,
            ),
            members=[],
            errors=[],
        ),
        merge_id="merge_transport",
        state=MergeOperationState.completed,
        open=False,
        participant_counts=MergeParticipantCounts(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        repos=[],
        operation_drift=[],
        preservation=None,
        publication_step=None,
    )


class FakeNative:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, bytes]] = []
        self.subscriptions: list[str] = []
        self.result_requests: list[str] = []
        self.merge_response_requests: list[str] = []

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

    def merge_operation_response(self, operation_id: str) -> bytes:
        self.merge_response_requests.append(operation_id)
        return encode_message("MergeResponse", merge_response())


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

    response = asyncio.run(bridge.merge_operation_response("op_transport"))

    assert response == merge_response()
    assert native.merge_response_requests == ["op_transport"]


def test_native_bridge_uses_wait_events_when_available() -> None:
    class WaitingNative(FakeNative):
        def __init__(self) -> None:
            super().__init__()
            self.waits: list[tuple[str, int, int]] = []

        def wait_events(
            self,
            operation_id: str,
            after_sequence: int,
            timeout_ms: int,
        ) -> tuple[list[bytes], bool]:
            self.waits.append((operation_id, after_sequence, timeout_ms))
            return [encode_message("OperationEvent", operation_event())], True

    native = WaitingNative()
    bridge = NativeCoreBridge(native=native)

    async def collect() -> list[OperationEvent]:
        return [event async for event in bridge.subscribe_events("op_transport")]

    assert asyncio.run(collect()) == [operation_event()]
    assert native.waits == [("op_transport", 0, _EVENT_WAIT_TIMEOUT_MS)]
    assert native.subscriptions == []


def test_native_bridge_maps_native_failures_to_bridge_error() -> None:
    class FailingNative(FakeNative):
        def call(self, *args: Any) -> bytes:
            raise RuntimeError("native exploded")

    bridge = NativeCoreBridge(native=FailingNative())

    with pytest.raises(GwzBridgeError, match="native bridge call failed"):
        asyncio.run(bridge.call("status", "StatusRequest", "StatusResponse", status_request()))


def test_native_bridge_preserves_structured_model_error_attributes() -> None:
    class FailingNative(FakeNative):
        def call(self, *args: Any) -> bytes:
            error = RuntimeError(
                "GitCommandFailed: member 'mem_a' at 'a': revspec 'feature/x' not found"
            )
            error.code = "GitCommandFailed"  # type: ignore[attr-defined]
            error.member_id = "mem_a"  # type: ignore[attr-defined]
            error.member_path = "a"  # type: ignore[attr-defined]
            error.target_kind = "Member"  # type: ignore[attr-defined]
            error.detail = None  # type: ignore[attr-defined]
            error.machine_message = (  # type: ignore[attr-defined]
                "member 'mem_a' at 'a': revspec 'feature/x' not found"
            )
            raise error

    bridge = NativeCoreBridge(native=FailingNative())

    with pytest.raises(GwzBridgeError) as exc_info:
        asyncio.run(bridge.call("merge", "StatusRequest", "StatusResponse", status_request()))

    error = exc_info.value
    assert error.code == "GitCommandFailed"
    assert error.member_id == "mem_a"
    assert error.member_path == "a"
    assert error.target_kind == "Member"
    assert error.machine_message == (
        "member 'mem_a' at 'a': revspec 'feature/x' not found"
    )


def test_native_bridge_maps_malformed_response_bytes_to_protocol_error() -> None:
    class BadBytesNative(FakeNative):
        def call(self, *args: Any) -> bytes:
            return b"not-cbor"

    bridge = NativeCoreBridge(native=BadBytesNative())

    with pytest.raises(GwzProtocolError, match="failed to decode StatusResponse"):
        asyncio.run(bridge.call("status", "StatusRequest", "StatusResponse", status_request()))


class SubmittedMergeBridge:
    def __init__(self, terminal: OperationResult | None = None) -> None:
        self.submissions: list[tuple[str, str, str, object]] = []
        self.response_lookups: list[str] = []
        self.terminal = terminal or OperationResult(
            operation_id="op_transport",
            request_id="req_transport",
            action=ActionKind.merge,
            aggregate_status=AggregateStatus.ok,
            started_at_ms=1,
            finished_at_ms=2,
            members=[],
            errors=[],
            attribution=None,
        )

    async def submit(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request: object,
    ) -> MergeResponse:
        self.submissions.append((method, request_message, response_message, request))
        return merge_response(AggregateStatus.accepted)

    def subscribe_events(self, operation_id: str):
        async def events():
            yield operation_event()

        return events()

    async def operation_result(self, operation_id: str) -> OperationResult:
        return self.terminal

    async def merge_operation_response(self, operation_id: str) -> MergeResponse:
        self.response_lookups.append(operation_id)
        if self.terminal.aggregate_status is AggregateStatus.failed:
            raise GwzBridgeError("operation completed without a merge response")
        return merge_response()


def test_merge_stream_returns_handle_events_and_retained_response() -> None:
    bridge = SubmittedMergeBridge()
    client = Client(root="/tmp/workspace", bridge=bridge)

    async def exercise() -> tuple[MergeOperationHandle, list[OperationEvent], MergeResponse]:
        handle = await client.merge_stream("feature/x", dry_run=True)
        events = [event async for event in handle.events()]
        return handle, events, await handle.result()

    handle, events, response = asyncio.run(exercise())

    assert handle.operation_id == "op_transport"
    assert events == [operation_event()]
    assert response == merge_response()
    assert bridge.submissions[0][:3] == ("merge", "MergeRequest", "MergeResponse")
    assert bridge.response_lookups == ["op_transport"]


def test_merge_stream_raises_from_structured_terminal_result() -> None:
    error = GwzError(
        code=GwzErrorCode.git_command_failed,
        message="member source is missing",
        member_id="mem_lib",
        member_path="lib",
        detail="source ref was not found",
        target_kind=TargetKind.member,
    )
    terminal = OperationResult(
        operation_id="op_transport",
        request_id="req_transport",
        action=ActionKind.merge,
        aggregate_status=AggregateStatus.failed,
        started_at_ms=1,
        finished_at_ms=2,
        members=[],
        errors=[error],
        attribution=None,
    )
    client = Client(root="/tmp/workspace", bridge=SubmittedMergeBridge(terminal))

    async def exercise() -> None:
        handle = await client.merge_stream("feature/x")
        with pytest.raises(GwzOperationError) as exc_info:
            await handle.result()
        assert exc_info.value.response is terminal
        assert exc_info.value.member_errors == [error]

    asyncio.run(exercise())
