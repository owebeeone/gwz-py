from __future__ import annotations

import asyncio
from typing import Any

import pytest

import gwz
from gwz import Client, GwzOperationError
from gwz.client import GwzErrorDetail
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    GwzError as GeneratedGwzError,
    GwzErrorCode,
    OperationResult,
    ResponseEnvelope,
    ResponseMeta,
    StatusResponse,
)


class ErrorBridge:
    def __init__(
        self,
        response: StatusResponse | None = None,
        result: OperationResult | None = None,
    ) -> None:
        self.response = response
        self.result = result

    async def call(self, method: str, request_message: str, response_message: str, request: Any) -> StatusResponse:
        assert self.response is not None
        return self.response

    async def operation_result(self, operation_id: str) -> OperationResult:
        assert self.result is not None
        return self.result

    def subscribe_events(self, operation_id: str) -> Any:
        async def _empty() -> Any:
            if False:
                yield None

        return _empty()


def error_detail(member_id: str = "member-1") -> GeneratedGwzError:
    return GeneratedGwzError(
        code=GwzErrorCode.dirty_member,
        message="member has uncommitted changes",
        member_id=member_id,
        member_path=f"packages/{member_id}",
        detail="git status reported dirty files",
        target_kind=None,
    )


def status_response(status: AggregateStatus, errors: list[GeneratedGwzError] | None = None) -> StatusResponse:
    return StatusResponse(
        response=ResponseEnvelope(
            meta=ResponseMeta(
                request_id="req_error",
                schema_version="gwz.protocol/v0",
                action=ActionKind.status,
                aggregate_status=status,
                operation_id="op_error",
                message=f"{status.name} status",
                attribution=None,
            ),
            members=[],
            errors=errors or [],
        ),
        workspace_git_status=None,
    )


def operation_result(status: AggregateStatus, errors: list[GeneratedGwzError] | None = None) -> OperationResult:
    return OperationResult(
        operation_id="op_result",
        request_id="req_result",
        action=ActionKind.status,
        aggregate_status=status,
        started_at_ms=1,
        finished_at_ms=2,
        members=[],
        errors=errors or [],
        attribution=None,
    )


@pytest.mark.parametrize(
    "status",
    [
        AggregateStatus.rejected,
        AggregateStatus.failed,
        AggregateStatus.partial,
        AggregateStatus.dirty,
        AggregateStatus.conflicted,
    ],
)
def test_high_level_methods_raise_for_unsuccessful_statuses(status: AggregateStatus) -> None:
    response = status_response(status)
    client = Client(bridge=ErrorBridge(response=response))

    with pytest.raises(GwzOperationError) as exc_info:
        asyncio.run(client.status())

    exc = exc_info.value
    assert str(exc) == f"{status.name} status"
    assert exc.response is response
    assert exc.aggregate_status is status
    assert exc.operation_id == "op_error"
    assert exc.request_id == "req_error"


def test_member_errors_preserved_from_response_envelope() -> None:
    member_errors = [error_detail("member-1"), error_detail("member-2")]
    response = status_response(AggregateStatus.failed, member_errors)
    client = Client(bridge=ErrorBridge(response=response))

    with pytest.raises(GwzOperationError) as exc_info:
        asyncio.run(client.status())

    exc = exc_info.value
    assert exc.response is response
    assert exc.member_errors is member_errors
    assert exc.member_errors == member_errors
    assert exc.member_errors[0] is member_errors[0]
    assert exc.aggregate_status is AggregateStatus.failed
    assert exc.operation_id == "op_error"
    assert exc.request_id == "req_error"


def test_member_errors_preserved_from_operation_result() -> None:
    member_errors = [error_detail("member-3")]
    result = operation_result(AggregateStatus.conflicted, member_errors)
    client = Client(bridge=ErrorBridge(result=result))

    with pytest.raises(GwzOperationError) as exc_info:
        asyncio.run(client.operation_result("op_result"))

    exc = exc_info.value
    assert str(exc) == "gwz operation returned conflicted"
    assert exc.response is result
    assert exc.member_errors is member_errors
    assert exc.member_errors == member_errors
    assert exc.member_errors[0] is member_errors[0]
    assert exc.aggregate_status is AggregateStatus.conflicted
    assert exc.operation_id == "op_result"
    assert exc.request_id == "req_result"


def test_stream_helpers_raise_with_final_operation_result() -> None:
    member_errors = [error_detail("member-4")]
    response = status_response(AggregateStatus.accepted)
    result = operation_result(AggregateStatus.dirty, member_errors)
    client = Client(bridge=ErrorBridge(response=response, result=result))

    async def drain() -> None:
        async for _event in client.materialize_stream("lock"):
            pass

    with pytest.raises(GwzOperationError) as exc_info:
        asyncio.run(drain())

    exc = exc_info.value
    assert exc.response is result
    assert exc.member_errors is member_errors
    assert exc.aggregate_status is AggregateStatus.dirty


def test_generated_gwz_error_alias_does_not_collide_with_package_error() -> None:
    assert gwz.GwzError is not GeneratedGwzError
    assert GwzErrorDetail is GeneratedGwzError
    assert "GwzErrorDetail" not in gwz.__all__
