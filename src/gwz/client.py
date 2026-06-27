from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Iterable, Sequence
from pathlib import Path
from typing import Any

from .bridge import CoreBridge, NativeCoreBridge
from .client_helpers import (
    enum_value as _enum_value,
    materialize_target as _target,
    raise_for_response,
    request_id as _request_id,
    sources as _sources,
)
from .protocol.generated import (
    AddExistingRepoRequest,
    AddExistingRepoResponse,
    BranchOp,
    BranchRequest,
    BranchResponse,
    CaptureRequest,
    CaptureResponse,
    CloneWorkspaceRequest,
    CloneWorkspaceResponse,
    CommitRequest,
    CommitResponse,
    CreateRepoRequest,
    CreateRepoResponse,
    CreateWorkspaceRequest,
    CreateWorkspaceResponse,
    DestructiveBehavior,
    GwzError as GwzErrorDetail,
    InitFromSourcesRequest,
    InitFromSourcesResponse,
    ListSnapshotsRequest,
    ListSnapshotsResponse,
    LsRequest,
    LsResponse,
    MaterializeRequest,
    MaterializeResponse,
    MaterializeTarget,
    MaterializeTargetKind,
    OperationAttribution,
    OperationEvent,
    OperationPolicy,
    OperationResult,
    PartialBehavior,
    PullHeadRequest,
    PullHeadResponse,
    PullSnapshotRequest,
    PullSnapshotResponse,
    PushRequest,
    PushResponse,
    RepoSyncRequest,
    RepoSyncResponse,
    RequestMeta,
    Selection,
    SnapshotSource,
    SnapshotSourceKind,
    SnapshotRequest,
    SnapshotResponse,
    SourceUrl,
    StageRequest,
    StageResponse,
    StatusMode,
    StatusPathStyle,
    StatusRequest,
    StatusResponse,
    StashOp,
    StashRequest,
    StashResponse,
    SyncBehavior,
    TagOp,
    TagRequest,
    TagResponse,
    UnsupportedMemberBehavior,
    WorkspaceRef,
)

SCHEMA_VERSION = "gwz.protocol/v0"


class Client:
    """Async Python facade over gwz-core protocol requests."""

    def __init__(self, root: str | Path | None = None, bridge: CoreBridge | None = None) -> None:
        self.root = Path(root).resolve() if root is not None else None
        self._bridge = bridge

    async def __aenter__(self) -> "Client":
        if self._bridge is None:
            self._bridge = NativeCoreBridge()
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        close = getattr(self._bridge, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    @property
    def bridge(self) -> CoreBridge:
        if self._bridge is None:
            self._bridge = NativeCoreBridge()
        return self._bridge

    def meta(
        self,
        *,
        request_id: str | None = None,
        root: str | Path | None = None,
        workspace_id: str | None = None,
        all_members: bool | None = None,
        member_ids: Iterable[str] = (),
        paths: Iterable[str] = (),
        targets: Iterable[str] = (),
        exclude_targets: Iterable[str] = (),
        dry_run: bool | None = None,
        partial: bool | None = None,
        destructive: bool | None = None,
        sync: SyncBehavior | str | None = None,
        unsupported_member: UnsupportedMemberBehavior | str | None = None,
        remote: str | None = None,
        concurrency: int | None = None,
        progress_min_interval_ms: int | None = None,
        max_connections_per_host: int | None = None,
        attribution: OperationAttribution | None = None,
    ) -> RequestMeta:
        selected_member_ids = list(member_ids)
        selected_paths = list(paths)
        selected_targets = list(targets)
        selected_exclude_targets = list(exclude_targets)
        if all_members is True and "@all" not in selected_targets:
            selected_targets.insert(0, "@all")
        selection = None
        if (
            all_members is not None
            or selected_member_ids
            or selected_paths
            or selected_targets
            or selected_exclude_targets
        ):
            selection = Selection(
                all=all_members,
                member_ids=selected_member_ids,
                paths=selected_paths,
                targets=selected_targets,
                exclude_targets=selected_exclude_targets,
            )

        policy = None
        if any(
            value is not None
            for value in (
                partial,
                destructive,
                sync,
                unsupported_member,
                remote,
                concurrency,
                progress_min_interval_ms,
                max_connections_per_host,
            )
        ):
            policy = OperationPolicy(
                partial=PartialBehavior.partial if partial else None,
                destructive=DestructiveBehavior.allow if destructive else None,
                sync=_enum_value(SyncBehavior, sync),
                unsupported_member=_enum_value(UnsupportedMemberBehavior, unsupported_member),
                remote=remote,
                concurrency=concurrency,
                progress_min_interval_ms=progress_min_interval_ms,
                max_connections_per_host=max_connections_per_host,
            )

        effective_root = Path(root).resolve() if root is not None else self.root
        workspace = None
        if effective_root is not None or workspace_id is not None:
            workspace = WorkspaceRef(
                root=str(effective_root) if effective_root is not None else None,
                workspace_id=workspace_id,
            )

        return RequestMeta(
            request_id=request_id or _request_id(),
            schema_version=SCHEMA_VERSION,
            workspace=workspace,
            selection=selection,
            policy=policy,
            dry_run=dry_run,
            attribution=attribution,
        )

    async def _call(self, method: str, request: Any, response_type: type[Any]) -> Any:
        result = await self.bridge.call(method, type(request).__name__, response_type.__name__, request)
        raise_for_response(result)
        return result

    async def _stream_call(
        self,
        method: str,
        request: Any,
        response_type: type[Any],
    ) -> AsyncIterator[OperationEvent]:
        submit = getattr(self.bridge, "submit", None)
        if submit is None:
            response = await self._call(method, request, response_type)
        else:
            response = await submit(method, type(request).__name__, response_type.__name__, request)
            raise_for_response(response)
        operation_id = getattr(getattr(response.response, "meta", None), "operation_id", None)
        if operation_id is None:
            return
        async for event in self.bridge.subscribe_events(operation_id):
            yield event
        await self.operation_result(operation_id)

    async def create_workspace(
        self,
        workspace_root: str | Path | None = None,
        *,
        workspace_id: str | None = None,
        **meta: Any,
    ) -> CreateWorkspaceResponse:
        root = Path(workspace_root).resolve() if workspace_root is not None else self.root
        request = CreateWorkspaceRequest(
            meta=self.meta(root=root, **meta),
            workspace_root=str(root or ""),
            workspace_id=workspace_id,
        )
        return await self._call("create_workspace", request, CreateWorkspaceResponse)

    async def init_from_sources(
        self,
        sources: Sequence[str | SourceUrl],
        *,
        workspace_root: str | Path | None = None,
        target: MaterializeTarget | None = None,
        workspace_id: str | None = None,
        **meta: Any,
    ) -> InitFromSourcesResponse:
        root = Path(workspace_root).resolve() if workspace_root is not None else self.root
        request = InitFromSourcesRequest(
            meta=self.meta(root=root, **meta),
            workspace_root=str(root or ""),
            sources=_sources(sources),
            target=target,
            workspace_id=workspace_id,
        )
        return await self._call("init_from_sources", request, InitFromSourcesResponse)

    def init_from_sources_stream(
        self,
        sources: Sequence[str | SourceUrl],
        *,
        workspace_root: str | Path | None = None,
        target: MaterializeTarget | None = None,
        workspace_id: str | None = None,
        **meta: Any,
    ) -> AsyncIterator[OperationEvent]:
        root = Path(workspace_root).resolve() if workspace_root is not None else self.root
        request = InitFromSourcesRequest(
            meta=self.meta(root=root, **meta),
            workspace_root=str(root or ""),
            sources=_sources(sources),
            target=target,
            workspace_id=workspace_id,
        )
        return self._stream_call("init_from_sources", request, InitFromSourcesResponse)

    async def clone_workspace(
        self,
        url: str,
        target: str | Path,
        **meta: Any,
    ) -> CloneWorkspaceResponse:
        request = CloneWorkspaceRequest(
            meta=self.meta(**meta),
            url=url,
            target=str(target),
        )
        return await self._call("clone_workspace", request, CloneWorkspaceResponse)

    def clone_workspace_stream(
        self,
        url: str,
        target: str | Path,
        **meta: Any,
    ) -> AsyncIterator[OperationEvent]:
        request = CloneWorkspaceRequest(
            meta=self.meta(**meta),
            url=url,
            target=str(target),
        )
        return self._stream_call("clone_workspace", request, CloneWorkspaceResponse)

    async def add_existing_repo(
        self,
        repository_path: str | Path,
        *,
        member_path: str | None = None,
        member_id: str | None = None,
        source_id: str | None = None,
        **meta: Any,
    ) -> AddExistingRepoResponse:
        request = AddExistingRepoRequest(
            meta=self.meta(**meta),
            repository_path=str(repository_path),
            member_path=member_path,
            member_id=member_id,
            source_id=source_id,
        )
        return await self._call("add_existing_repo", request, AddExistingRepoResponse)

    async def create_repo(
        self,
        member_path: str,
        *,
        initial_branch: str | None = None,
        member_id: str | None = None,
        source_id: str | None = None,
        **meta: Any,
    ) -> CreateRepoResponse:
        request = CreateRepoRequest(
            meta=self.meta(**meta),
            member_path=member_path,
            initial_branch=initial_branch,
            member_id=member_id,
            source_id=source_id,
        )
        return await self._call("create_repo", request, CreateRepoResponse)

    async def repo_sync(self, member_path: str | None = None, **meta: Any) -> RepoSyncResponse:
        if member_path is not None:
            if any(
                key in meta
                for key in (
                    "all_members",
                    "member_ids",
                    "paths",
                    "targets",
                    "exclude_targets",
                )
            ):
                raise ValueError("repo_sync member_path cannot be combined with explicit selection")
            meta["paths"] = [member_path]
        request = RepoSyncRequest(meta=self.meta(**meta))
        return await self._call("repo_sync", request, RepoSyncResponse)

    async def status(
        self,
        *,
        combined: bool = False,
        include_file_changes: bool | None = None,
        include_branch_summary: bool | None = None,
        path_style: StatusPathStyle | str | None = None,
        **meta: Any,
    ) -> StatusResponse:
        request = StatusRequest(
            meta=self.meta(**meta),
            mode=StatusMode.combined if combined else StatusMode.summary,
            include_file_changes=include_file_changes,
            include_branch_summary=include_branch_summary,
            path_style=_enum_value(StatusPathStyle, path_style),
        )
        return await self._call("status", request, StatusResponse)

    async def ls(self, *, include_unmaterialized: bool | None = True, **meta: Any) -> LsResponse:
        request = LsRequest(
            meta=self.meta(**meta),
            include_unmaterialized=include_unmaterialized,
        )
        return await self._call("ls", request, LsResponse)

    async def materialize(
        self,
        target: str | MaterializeTargetKind | MaterializeTarget = "lock",
        *,
        name: str | None = None,
        commit: str | None = None,
        **meta: Any,
    ) -> MaterializeResponse:
        materialize_target = target if isinstance(target, MaterializeTarget) else _target(target, name, commit)
        request = MaterializeRequest(meta=self.meta(**meta), target=materialize_target)
        return await self._call("materialize", request, MaterializeResponse)

    def materialize_stream(
        self,
        target: str | MaterializeTargetKind | MaterializeTarget = "lock",
        *,
        name: str | None = None,
        commit: str | None = None,
        **meta: Any,
    ) -> AsyncIterator[OperationEvent]:
        materialize_target = target if isinstance(target, MaterializeTarget) else _target(target, name, commit)
        request = MaterializeRequest(meta=self.meta(**meta), target=materialize_target)
        return self._stream_call("materialize", request, MaterializeResponse)

    async def switch(self, branch: str, **meta: Any) -> MaterializeResponse:
        return await self.materialize("branch", name=branch, **meta)

    async def snapshot(
        self,
        snapshot_id: str,
        *,
        source: SnapshotSource | None = None,
        branch: str | None = None,
        current_branch: bool = False,
        **meta: Any,
    ) -> SnapshotResponse:
        if source is not None and (branch is not None or current_branch):
            raise ValueError("snapshot source cannot be combined with branch/current_branch")
        if branch is not None:
            source = SnapshotSource(kind=SnapshotSourceKind.branch, branch=branch)
        elif current_branch:
            source = SnapshotSource(kind=SnapshotSourceKind.current, branch=None)
        request = SnapshotRequest(meta=self.meta(**meta), snapshot_id=snapshot_id, source=source)
        return await self._call("snapshot", request, SnapshotResponse)

    async def list_snapshots(self, **meta: Any) -> ListSnapshotsResponse:
        request = ListSnapshotsRequest(meta=self.meta(**meta))
        return await self._call("list_snapshots", request, ListSnapshotsResponse)

    async def tag(
        self,
        name: str | None = None,
        *,
        op: TagOp | str = TagOp.create,
        message: str | None = None,
        signed: bool | None = None,
        remote: str | None = None,
        all: bool | None = None,
        **meta: Any,
    ) -> TagResponse:
        request = TagRequest(
            meta=self.meta(**meta),
            op=_enum_value(TagOp, op),
            name=name,
            message=message,
            signed=signed,
            remote=remote,
            all=all,
        )
        return await self._call("tag", request, TagResponse)

    async def capture(self, **meta: Any) -> CaptureResponse:
        request = CaptureRequest(meta=self.meta(**meta))
        return await self._call("capture", request, CaptureResponse)

    async def commit(self, message: str, *, all: bool | None = None, **meta: Any) -> CommitResponse:
        request = CommitRequest(meta=self.meta(**meta), message=message, all=all)
        return await self._call("commit", request, CommitResponse)

    async def stage(
        self,
        pathspecs: Sequence[str] = (),
        *,
        cwd: str | Path | None = None,
        all: bool | None = None,
        **meta: Any,
    ) -> StageResponse:
        request = StageRequest(
            meta=self.meta(**meta),
            cwd=str(Path(cwd).resolve() if cwd is not None else Path.cwd()),
            pathspecs=list(pathspecs),
            all=all,
        )
        return await self._call("stage", request, StageResponse)

    async def pull_head(self, **meta: Any) -> PullHeadResponse:
        request = PullHeadRequest(meta=self.meta(**meta))
        return await self._call("pull_head", request, PullHeadResponse)

    def pull_head_stream(self, **meta: Any) -> AsyncIterator[OperationEvent]:
        request = PullHeadRequest(meta=self.meta(**meta))
        return self._stream_call("pull_head", request, PullHeadResponse)

    async def pull_snapshot(self, snapshot_id: str, **meta: Any) -> PullSnapshotResponse:
        request = PullSnapshotRequest(meta=self.meta(**meta), snapshot_id=snapshot_id)
        return await self._call("pull_snapshot", request, PullSnapshotResponse)

    def pull_snapshot_stream(self, snapshot_id: str, **meta: Any) -> AsyncIterator[OperationEvent]:
        request = PullSnapshotRequest(meta=self.meta(**meta), snapshot_id=snapshot_id)
        return self._stream_call("pull_snapshot", request, PullSnapshotResponse)

    async def push(
        self,
        *,
        remote: str | None = None,
        refspec: str | None = None,
        **meta: Any,
    ) -> PushResponse:
        request = PushRequest(meta=self.meta(**meta), remote=remote, refspec=refspec)
        return await self._call("push", request, PushResponse)

    def push_stream(
        self,
        *,
        remote: str | None = None,
        refspec: str | None = None,
        **meta: Any,
    ) -> AsyncIterator[OperationEvent]:
        request = PushRequest(meta=self.meta(**meta), remote=remote, refspec=refspec)
        return self._stream_call("push", request, PushResponse)

    async def stash(
        self,
        *,
        op: StashOp | str = StashOp.list,
        stash_id: str | None = None,
        message: str | None = None,
        include_untracked: bool | None = None,
        include_ignored: bool | None = None,
        expanded: bool | None = None,
        preserve_index: bool | None = None,
        **meta: Any,
    ) -> StashResponse:
        request = StashRequest(
            meta=self.meta(**meta),
            op=_enum_value(StashOp, op),
            stash_id=stash_id,
            message=message,
            include_untracked=include_untracked,
            include_ignored=include_ignored,
            expanded=expanded,
            preserve_index=preserve_index,
        )
        return await self._call("stash", request, StashResponse)

    async def branch(
        self,
        name: str | None = None,
        *,
        op: BranchOp | str = BranchOp.list,
        start_ref: str | None = None,
        source_ref: str | None = None,
        switch_after_create: bool | None = None,
        **meta: Any,
    ) -> BranchResponse:
        branch_op = _enum_value(BranchOp, op)
        effective_start_ref = source_ref if source_ref is not None else start_ref
        request = BranchRequest(
            meta=self.meta(**meta),
            op=branch_op,
            name=name,
            start_ref=effective_start_ref,
            switch_after_create=switch_after_create,
        )
        return await self._call("branch", request, BranchResponse)

    def events_subscribe(self, operation_id: str) -> AsyncIterator[OperationEvent]:
        return self.bridge.subscribe_events(operation_id)

    def events(self, operation_id: str) -> AsyncIterator[OperationEvent]:
        return self.events_subscribe(operation_id)

    async def operation_result(self, operation_id: str) -> OperationResult:
        result = await self.bridge.operation_result(operation_id)
        raise_for_response(result)
        return result


async def status(root: str | Path | None = None, **kwargs: Any) -> StatusResponse:
    async with Client(root=root) as client:
        return await client.status(**kwargs)


__all__ = ["Client", "GwzErrorDetail", "SCHEMA_VERSION", "status"]
