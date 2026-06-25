"""GENERATED typed client over a generic taut transport (call/subscribe)."""
from __future__ import annotations
from .api import *  # noqa: F401,F403

class GwzCoreClient:
    def __init__(self, transport):
        self._t = transport

    async def create_workspace(self, request: CreateWorkspaceRequest) -> CreateWorkspaceResponse:
        return await self._t.call("create_workspace", CreateWorkspaceResponse, request=request)

    async def init_from_sources(self, request: InitFromSourcesRequest) -> InitFromSourcesResponse:
        return await self._t.call("init_from_sources", InitFromSourcesResponse, request=request)

    async def add_existing_repo(self, request: AddExistingRepoRequest) -> AddExistingRepoResponse:
        return await self._t.call("add_existing_repo", AddExistingRepoResponse, request=request)

    async def create_repo(self, request: CreateRepoRequest) -> CreateRepoResponse:
        return await self._t.call("create_repo", CreateRepoResponse, request=request)

    async def repo_sync(self, request: RepoSyncRequest) -> RepoSyncResponse:
        return await self._t.call("repo_sync", RepoSyncResponse, request=request)

    async def materialize(self, request: MaterializeRequest) -> MaterializeResponse:
        return await self._t.call("materialize", MaterializeResponse, request=request)

    async def status(self, request: StatusRequest) -> StatusResponse:
        return await self._t.call("status", StatusResponse, request=request)

    async def ls(self, request: LsRequest) -> LsResponse:
        return await self._t.call("ls", LsResponse, request=request)

    async def snapshot(self, request: SnapshotRequest) -> SnapshotResponse:
        return await self._t.call("snapshot", SnapshotResponse, request=request)

    async def tag(self, request: TagRequest) -> TagResponse:
        return await self._t.call("tag", TagResponse, request=request)

    async def capture(self, request: CaptureRequest) -> CaptureResponse:
        return await self._t.call("capture", CaptureResponse, request=request)

    async def commit(self, request: CommitRequest) -> CommitResponse:
        return await self._t.call("commit", CommitResponse, request=request)

    async def stage(self, request: StageRequest) -> StageResponse:
        return await self._t.call("stage", StageResponse, request=request)

    async def pull_head(self, request: PullHeadRequest) -> PullHeadResponse:
        return await self._t.call("pull_head", PullHeadResponse, request=request)

    async def pull_snapshot(self, request: PullSnapshotRequest) -> PullSnapshotResponse:
        return await self._t.call("pull_snapshot", PullSnapshotResponse, request=request)

    async def push(self, request: PushRequest) -> PushResponse:
        return await self._t.call("push", PushResponse, request=request)

    async def stash(self, request: StashRequest) -> StashResponse:
        return await self._t.call("stash", StashResponse, request=request)

    async def branch(self, request: BranchRequest) -> BranchResponse:
        return await self._t.call("branch", BranchResponse, request=request)

    def events_subscribe(self, operation_id: str):  # log stream
        return self._t.subscribe("events.subscribe", operation_id=operation_id)

    async def operation_result(self, operation_id: str) -> OperationResult:
        return await self._t.call("operation.result", OperationResult, operation_id=operation_id)

