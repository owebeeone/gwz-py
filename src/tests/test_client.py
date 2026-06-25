from __future__ import annotations

import inspect
import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from gwz import Client
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    LsResponse,
    ResponseEnvelope,
    ResponseMeta,
    StatusMode,
    StatusRequest,
    StatusResponse,
)


def ok_response(response_type: type[Any]) -> Any:
    return response_type(
        response=ResponseEnvelope(
            meta=ResponseMeta(
                request_id="req_test",
                schema_version="gwz.protocol/v0",
                action=ActionKind.status,
                aggregate_status=AggregateStatus.ok,
                operation_id="op_test",
                message="ok",
                attribution=None,
            ),
            members=[],
            errors=[],
        ),
        **({"workspace_git_status": None} if response_type is StatusResponse else {}),
        **({"members": []} if response_type is LsResponse else {}),
    )


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, Any]] = []

    async def call(self, method: str, request_message: str, response_message: str, request: Any) -> Any:
        self.calls.append((method, request_message, response_message, request))
        response_type = StatusResponse if response_message == "StatusResponse" else LsResponse
        return ok_response(response_type)

    async def stream(
        self,
        method: str,
        request_message: str,
        event_message: str,
        request: Any,
    ) -> AsyncIterator[Any]:
        if False:
            yield None


def test_status_builds_taut_request() -> None:
    bridge = FakeBridge()
    client = Client(root=Path("/tmp/workspace"), bridge=bridge)

    response = asyncio.run(client.status(combined=True))

    assert isinstance(response, StatusResponse)
    method, request_message, response_message, request = bridge.calls[0]
    assert method == "status"
    assert request_message == "StatusRequest"
    assert response_message == "StatusResponse"
    assert isinstance(request, StatusRequest)
    assert request.mode is StatusMode.combined
    assert request.meta.schema_version == "gwz.protocol/v0"
    assert request.meta.workspace is not None
    assert request.meta.workspace.root == str(Path("/tmp/workspace").resolve())


def test_public_operations_are_async() -> None:
    async_methods = [
        "add_existing_repo",
        "capture",
        "commit",
        "create_repo",
        "create_workspace",
        "init_from_sources",
        "ls",
        "materialize",
        "pull_head",
        "pull_snapshot",
        "push",
        "snapshot",
        "stage",
        "status",
        "tag",
    ]
    for name in async_methods:
        assert inspect.iscoroutinefunction(getattr(Client, name)), name
