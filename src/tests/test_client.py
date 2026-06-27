from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from gwz import Client
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    BranchOp,
    BranchRequest,
    BranchResponse,
    CloneWorkspaceRequest,
    CloneWorkspaceResponse,
    InitFromSourcesResponse,
    ListSnapshotsRequest,
    ListSnapshotsResponse,
    LsResponse,
    MaterializeRequest,
    MaterializeResponse,
    MaterializeTargetKind,
    OperationResult,
    PullHeadResponse,
    PullSnapshotResponse,
    PushResponse,
    RepoSyncRequest,
    RepoSyncResponse,
    ResponseEnvelope,
    ResponseMeta,
    SnapshotRequest,
    SnapshotResponse,
    SnapshotSourceKind,
    StashResponse,
    StatusMode,
    StatusRequest,
    StatusResponse,
    TagResponse,
)


RESPONSE_TYPES = {
    cls.__name__: cls
    for cls in (
        BranchResponse,
        CloneWorkspaceResponse,
        InitFromSourcesResponse,
        ListSnapshotsResponse,
        LsResponse,
        MaterializeResponse,
        PullHeadResponse,
        PullSnapshotResponse,
        PushResponse,
        RepoSyncResponse,
        SnapshotResponse,
        StashResponse,
        StatusResponse,
        TagResponse,
    )
}

RESPONSE_EXTRAS = {
    BranchResponse: {"repos": None},
    ListSnapshotsResponse: {"snapshots": None},
    LsResponse: {"members": []},
    StashResponse: {"bundles": None},
    StatusResponse: {"workspace_git_status": None},
    TagResponse: {"tags": None},
}


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
        **RESPONSE_EXTRAS.get(response_type, {}),
    )


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, Any]] = []
        self.subscriptions: list[str] = []

    async def call(self, method: str, request_message: str, response_message: str, request: Any) -> Any:
        self.calls.append((method, request_message, response_message, request))
        response_type = RESPONSE_TYPES[response_message]
        return ok_response(response_type)

    def subscribe_events(self, operation_id: str) -> AsyncIterator[Any]:
        self.subscriptions.append(operation_id)

        async def _empty() -> AsyncIterator[Any]:
            if False:
                yield None

        return _empty()

    async def operation_result(self, operation_id: str) -> OperationResult:
        return OperationResult(
            operation_id=operation_id,
            request_id="req_test",
            action=ActionKind.status,
            aggregate_status=AggregateStatus.ok,
            started_at_ms=0,
            finished_at_ms=1,
            members=[],
            errors=[],
            attribution=None,
        )


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


def test_meta_builds_target_selection_fields() -> None:
    client = Client(root=Path("/tmp/workspace"), bridge=FakeBridge())

    meta = client.meta(
        targets=("@root", "mem_app"),
        exclude_targets=("@default", "repos/old"),
    )

    assert meta.selection is not None
    assert meta.selection.all is None
    assert meta.selection.member_ids == []
    assert meta.selection.paths == []
    assert meta.selection.targets == ["@root", "mem_app"]
    assert meta.selection.exclude_targets == ["@default", "repos/old"]


def test_meta_all_members_maps_to_all_target_and_keeps_legacy_flag() -> None:
    client = Client(root=Path("/tmp/workspace"), bridge=FakeBridge())

    meta = client.meta(all_members=True)

    assert meta.selection is not None
    assert meta.selection.all is True
    assert meta.selection.member_ids == []
    assert meta.selection.paths == []
    assert meta.selection.targets == ["@all"]
    assert meta.selection.exclude_targets == []


def test_meta_keeps_legacy_selection_fields() -> None:
    client = Client(root=Path("/tmp/workspace"), bridge=FakeBridge())

    meta = client.meta(member_ids=("mem_app",), paths=("packages/app",))

    assert meta.selection is not None
    assert meta.selection.all is None
    assert meta.selection.member_ids == ["mem_app"]
    assert meta.selection.paths == ["packages/app"]
    assert meta.selection.targets == []
    assert meta.selection.exclude_targets == []


def test_repo_sync_member_path_uses_selection() -> None:
    bridge = FakeBridge()
    client = Client(root=Path("/tmp/workspace"), bridge=bridge)

    asyncio.run(client.repo_sync("packages/app"))

    method, _, _, request = bridge.calls[0]
    assert method == "repo_sync"
    assert isinstance(request, RepoSyncRequest)
    assert request.meta.selection is not None
    assert request.meta.selection.paths == ["packages/app"]


def test_repo_sync_explicit_paths_use_selection() -> None:
    bridge = FakeBridge()
    client = Client(root=Path("/tmp/workspace"), bridge=bridge)

    asyncio.run(client.repo_sync(paths=("packages/app", "libs/core")))

    method, _, _, request = bridge.calls[0]
    assert method == "repo_sync"
    assert isinstance(request, RepoSyncRequest)
    assert request.meta.selection is not None
    assert request.meta.selection.paths == ["packages/app", "libs/core"]


def test_snapshot_branch_source_is_explicit() -> None:
    bridge = FakeBridge()
    client = Client(root=Path("/tmp/workspace"), bridge=bridge)

    asyncio.run(client.snapshot("release-cut", branch="release/1"))

    method, _, _, request = bridge.calls[0]
    assert method == "snapshot"
    assert isinstance(request, SnapshotRequest)
    assert request.source is not None
    assert request.source.kind is SnapshotSourceKind.branch
    assert request.source.branch == "release/1"


def test_list_snapshots_builds_taut_request() -> None:
    bridge = FakeBridge()
    client = Client(root=Path("/tmp/workspace"), bridge=bridge)

    response = asyncio.run(client.list_snapshots())

    assert isinstance(response, ListSnapshotsResponse)
    method, request_message, response_message, request = bridge.calls[0]
    assert method == "list_snapshots"
    assert request_message == "ListSnapshotsRequest"
    assert response_message == "ListSnapshotsResponse"
    assert isinstance(request, ListSnapshotsRequest)
    assert request.meta.workspace is not None
    assert request.meta.workspace.root == str(Path("/tmp/workspace").resolve())


def test_branch_merge_source_maps_to_start_ref() -> None:
    bridge = FakeBridge()
    client = Client(root=Path("/tmp/workspace"), bridge=bridge)

    asyncio.run(client.branch(op="merge", source_ref="refs/heads/topic"))

    method, _, _, request = bridge.calls[0]
    assert method == "branch"
    assert isinstance(request, BranchRequest)
    assert request.op is BranchOp.merge
    assert request.name is None
    assert request.start_ref == "refs/heads/topic"


def test_branch_create_does_not_inject_head_start_ref() -> None:
    bridge = FakeBridge()
    client = Client(root=Path("/tmp/workspace"), bridge=bridge)

    asyncio.run(client.branch("feature/new", op="create"))

    method, _, _, request = bridge.calls[0]
    assert method == "branch"
    assert isinstance(request, BranchRequest)
    assert request.op is BranchOp.create
    assert request.name == "feature/new"
    assert request.start_ref is None


def test_clone_workspace_builds_taut_request() -> None:
    bridge = FakeBridge()
    client = Client(root=Path("/tmp/workspace"), bridge=bridge)

    response = asyncio.run(client.clone_workspace("git@example.invalid:org/ws.git", "work/ws"))

    assert isinstance(response, CloneWorkspaceResponse)
    method, request_message, response_message, request = bridge.calls[0]
    assert method == "clone_workspace"
    assert request_message == "CloneWorkspaceRequest"
    assert response_message == "CloneWorkspaceResponse"
    assert isinstance(request, CloneWorkspaceRequest)
    assert request.url == "git@example.invalid:org/ws.git"
    assert request.target == "work/ws"


def test_materialize_branch_switch_uses_branch_target() -> None:
    bridge = FakeBridge()
    client = Client(root=Path("/tmp/workspace"), bridge=bridge)

    asyncio.run(client.switch("feature/new"))

    method, _, _, request = bridge.calls[0]
    assert method == "materialize"
    assert isinstance(request, MaterializeRequest)
    assert request.target.kind is MaterializeTargetKind.branch
    assert request.target.name == "feature/new"
    assert request.target.commit is None


def test_events_subscribe_delegates_by_operation_id() -> None:
    bridge = FakeBridge()
    client = Client(root=Path("/tmp/workspace"), bridge=bridge)

    event_stream = client.events_subscribe("op_manual")

    assert event_stream.__aiter__() is event_stream
    assert bridge.subscriptions == ["op_manual"]


def test_materialize_stream_subscribes_by_operation_id() -> None:
    bridge = FakeBridge()
    client = Client(root=Path("/tmp/workspace"), bridge=bridge)

    async def drain() -> None:
        async for _event in client.materialize_stream("lock"):
            pass

    asyncio.run(drain())

    assert bridge.calls[0][0] == "materialize"
    assert bridge.subscriptions == ["op_test"]


def test_stream_helpers_subscribe_by_operation_id() -> None:
    stream_calls = [
        ("clone_workspace", lambda client: client.clone_workspace_stream("file:///tmp/source", "workspace")),
        ("init_from_sources", lambda client: client.init_from_sources_stream(["file:///tmp/source"])),
        ("pull_head", lambda client: client.pull_head_stream()),
        ("pull_snapshot", lambda client: client.pull_snapshot_stream("snap_one")),
        ("push", lambda client: client.push_stream(remote="origin")),
    ]
    for method, stream in stream_calls:
        bridge = FakeBridge()
        client = Client(root=Path("/tmp/workspace"), bridge=bridge)

        async def drain() -> None:
            async for _event in stream(client):
                pass

        asyncio.run(drain())

        assert bridge.calls[0][0] == method
        assert bridge.subscriptions == ["op_test"]


def test_operation_result_delegates_to_bridge() -> None:
    bridge = FakeBridge()
    client = Client(root=Path("/tmp/workspace"), bridge=bridge)

    result = asyncio.run(client.operation_result("op_test"))

    assert result.operation_id == "op_test"


def test_forall_is_not_a_client_service_method() -> None:
    assert not hasattr(Client, "forall")


def test_public_operations_are_async() -> None:
    async_methods = [
        "add_existing_repo",
        "branch",
        "capture",
        "clone_workspace",
        "commit",
        "create_repo",
        "create_workspace",
        "init_from_sources",
        "list_snapshots",
        "ls",
        "materialize",
        "operation_result",
        "pull_head",
        "pull_snapshot",
        "push",
        "repo_sync",
        "snapshot",
        "stage",
        "stash",
        "status",
        "switch",
        "tag",
    ]
    for name in async_methods:
        assert inspect.iscoroutinefunction(getattr(Client, name)), name
