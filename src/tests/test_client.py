from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from gwz import Client
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    BranchOp,
    BranchRequest,
    BranchResponse,
    AttachRepoMemberRequest,
    AttachRepoMemberResponse,
    CloneWorkspaceRequest,
    CloneWorkspaceResponse,
    CloneRepoMemberRequest,
    CloneRepoMemberResponse,
    CommitRequest,
    CommitResponse,
    CreateWorkspaceRequest,
    CreateWorkspaceResponse,
    DetachRepoMemberRequest,
    DetachRepoMemberResponse,
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
        AttachRepoMemberResponse,
        CloneRepoMemberResponse,
        CloneWorkspaceResponse,
        CommitResponse,
        CreateWorkspaceResponse,
        DetachRepoMemberResponse,
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


def test_create_workspace_without_root_defaults_to_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    bridge = FakeBridge()
    client = Client(bridge=bridge)

    response = asyncio.run(client.create_workspace())

    assert isinstance(response, CreateWorkspaceResponse)
    method, request_message, response_message, request = bridge.calls[0]
    assert method == "create_workspace"
    assert request_message == "CreateWorkspaceRequest"
    assert response_message == "CreateWorkspaceResponse"
    assert isinstance(request, CreateWorkspaceRequest)
    assert request.workspace_root == str(tmp_path.resolve())
    assert request.meta.workspace is not None
    assert request.meta.workspace.root == str(tmp_path.resolve())


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


def test_commit_builds_marker_tristate_request() -> None:
    bridge = FakeBridge()
    client = Client(root=Path("/tmp/workspace"), bridge=bridge)

    response = asyncio.run(client.commit("message", all=True, commit_marker=False))

    assert isinstance(response, CommitResponse)
    method, request_message, response_message, request = bridge.calls[0]
    assert method == "commit"
    assert request_message == "CommitRequest"
    assert response_message == "CommitResponse"
    assert isinstance(request, CommitRequest)
    assert request.message == "message"
    assert request.all is True
    assert request.commit_marker is False


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


def test_repo_lifecycle_builds_taut_requests_with_cli_parity() -> None:
    bridge = FakeBridge()
    client = Client(root=Path("/tmp/workspace"), bridge=bridge)

    cloned = asyncio.run(
        client.clone_repo_member(
            "git@example.invalid:org/shared.git",
            "libs/shared",
            member_id="mem_shared_v2",
            source_id="src_shared",
            dry_run=True,
        )
    )
    detached = asyncio.run(client.detach_repo_member("libs/shared", dry_run=True))
    attached = asyncio.run(client.attach_repo_member("mem_shared", dry_run=True))

    assert isinstance(cloned, CloneRepoMemberResponse)
    assert isinstance(detached, DetachRepoMemberResponse)
    assert isinstance(attached, AttachRepoMemberResponse)

    clone_method, clone_request_name, clone_response_name, clone_request = bridge.calls[0]
    assert clone_method == "clone_repo_member"
    assert clone_request_name == "CloneRepoMemberRequest"
    assert clone_response_name == "CloneRepoMemberResponse"
    assert isinstance(clone_request, CloneRepoMemberRequest)
    assert clone_request.source.url == "git@example.invalid:org/shared.git"
    assert clone_request.source.path == "libs/shared"
    assert clone_request.member_id == "mem_shared_v2"
    assert clone_request.source_id == "src_shared"
    assert clone_request.meta.dry_run is True

    detach_method, _, _, detach_request = bridge.calls[1]
    assert detach_method == "detach_repo_member"
    assert isinstance(detach_request, DetachRepoMemberRequest)
    assert detach_request.meta.selection is not None
    assert detach_request.meta.selection.targets == ["libs/shared"]

    attach_method, _, _, attach_request = bridge.calls[2]
    assert attach_method == "attach_repo_member"
    assert isinstance(attach_request, AttachRepoMemberRequest)
    assert attach_request.meta.selection is not None
    assert attach_request.meta.selection.targets == ["mem_shared"]


def test_repo_lifecycle_operands_reject_explicit_selection() -> None:
    client = Client(root=Path("/tmp/workspace"), bridge=FakeBridge())

    with pytest.raises(ValueError, match="cannot be combined with explicit selection"):
        asyncio.run(client.detach_repo_member("mem_shared", targets=("mem_other",)))
    with pytest.raises(ValueError, match="cannot be combined with explicit selection"):
        asyncio.run(client.attach_repo_member("mem_shared", all_members=True))


def test_attach_repo_member_rejects_path_operand() -> None:
    client = Client(root=Path("/tmp/workspace"), bridge=FakeBridge())

    with pytest.raises(ValueError, match="member id"):
        asyncio.run(client.attach_repo_member("libs/shared"))


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
        ("clone_repo_member", lambda client: client.clone_repo_member_stream("file:///tmp/source", "libs/source")),
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


def test_clone_repo_member_stream_prefers_submit_route() -> None:
    class SubmitBridge(FakeBridge):
        def __init__(self) -> None:
            super().__init__()
            self.submissions: list[tuple[str, str, str, Any]] = []

        async def submit(
            self,
            method: str,
            request_message: str,
            response_message: str,
            request: Any,
        ) -> Any:
            self.submissions.append(
                (method, request_message, response_message, request)
            )
            return ok_response(RESPONSE_TYPES[response_message])

    bridge = SubmitBridge()
    client = Client(root=Path("/tmp/workspace"), bridge=bridge)

    async def drain() -> None:
        async for _event in client.clone_repo_member_stream(
            "file:///tmp/source", "libs/source"
        ):
            pass

    asyncio.run(drain())

    assert bridge.calls == []
    assert bridge.submissions[0][:3] == (
        "clone_repo_member",
        "CloneRepoMemberRequest",
        "CloneRepoMemberResponse",
    )
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
        "attach_repo_member",
        "capture",
        "clone_workspace",
        "clone_repo_member",
        "commit",
        "create_repo",
        "create_workspace",
        "detach_repo_member",
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
