from __future__ import annotations

import dataclasses
import inspect
import uuid
from collections.abc import AsyncIterator, Iterable, Sequence
from pathlib import Path
from typing import Any

from .bridge import CoreBridge, NativeCoreBridge
from .errors import GwzOperationError
from .protocol.generated import (
    AddExistingRepoRequest,
    AddExistingRepoResponse,
    CaptureRequest,
    CaptureResponse,
    CommitRequest,
    CommitResponse,
    CreateRepoRequest,
    CreateRepoResponse,
    CreateWorkspaceRequest,
    CreateWorkspaceResponse,
    DestructiveBehavior,
    InitFromSourcesRequest,
    InitFromSourcesResponse,
    LsRequest,
    LsResponse,
    MaterializeRequest,
    MaterializeResponse,
    MaterializeTarget,
    MaterializeTargetKind,
    OperationAttribution,
    OperationEvent,
    OperationPolicy,
    PartialBehavior,
    PullHeadRequest,
    PullHeadResponse,
    PullSnapshotRequest,
    PullSnapshotResponse,
    PushRequest,
    PushResponse,
    RequestMeta,
    Selection,
    SnapshotRequest,
    SnapshotResponse,
    SourceUrl,
    StageRequest,
    StageResponse,
    StatusMode,
    StatusPathStyle,
    StatusRequest,
    StatusResponse,
    SyncBehavior,
    TagOp,
    TagRequest,
    TagResponse,
    UnsupportedMemberBehavior,
    WorkspaceRef,
)

SCHEMA_VERSION = "gwz.protocol/v0"


def _request_id() -> str:
    return f"req_{uuid.uuid4().hex}"


def _enum_value(enum_type: type[Any], value: Any) -> Any:
    if value is None or isinstance(value, enum_type):
        return value
    return enum_type[value]


def _target(kind: str | MaterializeTargetKind, name: str | None = None, commit: str | None = None) -> MaterializeTarget:
    if isinstance(kind, str):
        kind = MaterializeTargetKind[kind]
    return MaterializeTarget(kind=kind, name=name, commit=commit)


def _sources(sources: Sequence[str | SourceUrl]) -> list[SourceUrl]:
    result: list[SourceUrl] = []
    for source in sources:
        if isinstance(source, SourceUrl):
            result.append(source)
        else:
            result.append(SourceUrl(url=str(source), path=None, remote_name=None, branch=None))
    return result


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
        selection = None
        if all_members is not None or selected_member_ids or selected_paths:
            selection = Selection(
                all=all_members,
                member_ids=selected_member_ids,
                paths=selected_paths,
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
        self._raise_for_response(result)
        return result

    def _stream(self, method: str, request: Any) -> AsyncIterator[OperationEvent]:
        return self.bridge.stream(method, type(request).__name__, "OperationEvent", request)

    def _raise_for_response(self, response: Any) -> None:
        envelope = getattr(response, "response", None)
        meta = getattr(envelope, "meta", None)
        aggregate = getattr(meta, "aggregate_status", None)
        if aggregate is None:
            return
        name = getattr(aggregate, "name", str(aggregate))
        if name in {"accepted", "ok", "noop"}:
            return
        message = getattr(meta, "message", None) or f"gwz operation returned {name}"
        raise GwzOperationError(
            message=message,
            response=response,
            aggregate_status=aggregate,
            operation_id=getattr(meta, "operation_id", None),
            request_id=getattr(meta, "request_id", None),
        )

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
        return self._stream("materialize", request)

    async def snapshot(self, snapshot_id: str, **meta: Any) -> SnapshotResponse:
        request = SnapshotRequest(meta=self.meta(**meta), snapshot_id=snapshot_id)
        return await self._call("snapshot", request, SnapshotResponse)

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

    async def pull_snapshot(self, snapshot_id: str, **meta: Any) -> PullSnapshotResponse:
        request = PullSnapshotRequest(meta=self.meta(**meta), snapshot_id=snapshot_id)
        return await self._call("pull_snapshot", request, PullSnapshotResponse)

    async def push(
        self,
        *,
        remote: str | None = None,
        refspec: str | None = None,
        **meta: Any,
    ) -> PushResponse:
        request = PushRequest(meta=self.meta(**meta), remote=remote, refspec=refspec)
        return await self._call("push", request, PushResponse)


async def status(root: str | Path | None = None, **kwargs: Any) -> StatusResponse:
    async with Client(root=root) as client:
        return await client.status(**kwargs)


__all__ = ["Client", "SCHEMA_VERSION", "status"]
