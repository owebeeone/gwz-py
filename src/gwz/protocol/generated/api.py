"""GENERATED native Python types — do not edit."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum

class ActionKind(Enum):
    create_workspace = 0
    init_from_sources = 1
    add_existing_repo = 2
    create_repo = 3
    materialize = 4
    status = 5
    snapshot = 6
    tag = 7
    pull_head = 8
    pull_snapshot = 9
    push = 10
    capture = 11
    commit = 12
    stage = 13
    ls = 14
    forall = 15

class TagOp(Enum):
    create = 0
    list = 1
    fetch = 2
    push = 3
    delete = 4

class ExecMode(Enum):
    argv = 0
    shell = 1

class SourceKind(Enum):
    git = 0
    archive = 1
    package = 2
    local = 3
    generated = 4

class AggregateStatus(Enum):
    accepted = 0
    ok = 1
    noop = 2
    rejected = 3
    partial = 4
    failed = 5
    dirty = 6
    conflicted = 7

class MemberStatus(Enum):
    planned = 0
    ok = 1
    noop = 2
    skipped = 3
    rejected = 4
    failed = 5
    conflicted = 6

class MaterializeTargetKind(Enum):
    lock = 0
    head = 1
    snapshot = 2
    tag = 3
    commit = 4

class SyncBehavior(Enum):
    fetch_only = 0
    ff_only = 1
    merge = 2
    rebase = 3
    reset = 4
    driver_selected = 5

class PartialBehavior(Enum):
    atomic = 0
    partial = 1

class DestructiveBehavior(Enum):
    refuse = 0
    allow = 1

class UnsupportedMemberBehavior(Enum):
    fail = 0
    skip = 1

class PlannedAction(Enum):
    noop = 0
    clone = 1
    fetch = 2
    fast_forward = 3
    checkout = 4
    init_repo = 5
    add_manifest_member = 6
    write_manifest = 7
    write_lock = 8
    write_snapshot = 9
    write_tag = 10
    push = 11
    merge = 12
    rebase = 13
    reset = 14

class LockMatch(Enum):
    unknown = 0
    matches = 1
    differs = 2
    missing = 3

class GitProgressPhase(Enum):
    enumerating = 0
    counting = 1
    compressing = 2
    receiving = 3
    resolving = 4
    checking_out = 5
    writing = 6

class StatusMode(Enum):
    summary = 0
    combined = 1

class StatusPathStyle(Enum):
    member_relative = 0
    workspace_relative = 1

class EventKind(Enum):
    operation_started = 0
    member_started = 1
    member_progress = 2
    member_finished = 3
    artifact_written = 4
    operation_finished = 5
    reset = 6

class Severity(Enum):
    debug = 0
    info = 1
    warn = 2
    error = 3

class GwzErrorCode(Enum):
    ok = 0
    invalid_request = 1
    workspace_not_found = 2
    workspace_already_exists = 3
    nested_workspace = 4
    manifest_not_found = 5
    manifest_invalid = 6
    schema_unsupported = 7
    member_not_found = 8
    member_inactive = 9
    path_escape = 10
    path_collision = 11
    path_reserved = 12
    unsupported_source_kind = 13
    unsupported_operation = 14
    dirty_member = 15
    diverged_member = 16
    missing_remote = 17
    snapshot_not_found = 18
    lock_not_found = 19
    tag_not_found = 20
    tag_invalid = 21
    remote_rejected = 22
    git_command_failed = 23
    external_tool_missing = 24
    operation_not_found = 25
    attribution_denied = 26
    permission_denied = 27
    io_error = 28
    internal_error = 29

@dataclass(slots=True)
class WorkspaceRef:
    root: str | None
    workspace_id: str | None

@dataclass(slots=True)
class OperationActor:
    actor_id: str
    display_name: str | None
    email: str | None
    authority: str | None

@dataclass(slots=True)
class GitObjectIdentity:
    name: str
    email: str
    time_ms: int | None
    timezone_offset_minutes: int | None

@dataclass(slots=True)
class OperationAttribution:
    actor: OperationActor | None
    git_author: GitObjectIdentity | None
    git_committer: GitObjectIdentity | None
    credential_ref: str | None

@dataclass(slots=True)
class Selection:
    all: bool | None
    member_ids: list[str]
    paths: list[str]

@dataclass(slots=True)
class OperationPolicy:
    partial: PartialBehavior | None
    destructive: DestructiveBehavior | None
    sync: SyncBehavior | None
    unsupported_member: UnsupportedMemberBehavior | None
    remote: str | None
    concurrency: int | None
    progress_min_interval_ms: int | None
    max_connections_per_host: int | None

@dataclass(slots=True)
class RequestMeta:
    request_id: str
    schema_version: str
    workspace: WorkspaceRef | None
    selection: Selection | None
    policy: OperationPolicy | None
    dry_run: bool | None
    attribution: OperationAttribution | None

@dataclass(slots=True)
class ResponseMeta:
    request_id: str
    schema_version: str
    action: ActionKind
    aggregate_status: AggregateStatus
    operation_id: str | None
    message: str | None
    attribution: OperationAttribution | None

@dataclass(slots=True)
class GwzError:
    code: GwzErrorCode
    message: str
    member_id: str | None
    member_path: str | None
    detail: str | None

@dataclass(slots=True)
class RemoteSpec:
    name: str
    url: str
    fetch: bool | None
    push: bool | None

@dataclass(slots=True)
class DesiredRef:
    branch: str | None
    commit: str | None
    git_tag: str | None
    local_only: bool | None

@dataclass(slots=True)
class SourceUrl:
    url: str
    path: str | None
    remote_name: str | None
    branch: str | None

@dataclass(slots=True)
class MemberSpec:
    member_id: str
    path: str
    source_id: str
    source_kind: SourceKind
    active: bool
    desired: DesiredRef | None
    remotes: list[RemoteSpec]

@dataclass(slots=True)
class MaterializeTarget:
    kind: MaterializeTargetKind
    name: str | None
    commit: str | None

@dataclass(slots=True)
class ResolvedMemberState:
    member_id: str
    path: str
    source_id: str
    source_kind: SourceKind
    commit: str | None
    branch: str | None
    detached: bool | None
    upstream: str | None
    dirty: bool | None
    materialized: bool
    remotes: list[RemoteSpec]

@dataclass(slots=True)
class GitStatus:
    member_id: str
    branch: str | None
    detached: bool
    head: str | None
    upstream: str | None
    ahead: int | None
    behind: int | None
    staged: int
    unstaged: int
    untracked: int
    dirty: bool

@dataclass(slots=True)
class GitFileChange:
    member_id: str
    member_path: str
    repo_path: str
    workspace_path: str
    index_status: str
    worktree_status: str
    original_repo_path: str | None

@dataclass(slots=True)
class GitTransferProgress:
    phase: GitProgressPhase
    received_objects: int | None
    total_objects: int | None
    received_bytes: int | None
    indexed_deltas: int | None
    total_deltas: int | None

@dataclass(slots=True)
class WorkspaceRootGitStatus:
    branch: str | None
    detached: bool
    head: str | None
    staged: int
    unstaged: int
    untracked: int
    dirty: bool
    unborn: bool

@dataclass(slots=True)
class WorkspaceRootFileChange:
    repo_path: str
    workspace_path: str
    index_status: str
    worktree_status: str
    original_repo_path: str | None

@dataclass(slots=True)
class GitMemberBranchStatus:
    member_id: str
    member_path: str
    label: str
    branch: str | None
    detached: bool
    unborn: bool
    head: str | None
    upstream: str | None
    ahead: int | None
    behind: int | None

@dataclass(slots=True)
class GitBranchGroup:
    label: str
    member_ids: list[str]
    member_paths: list[str]

@dataclass(slots=True)
class GitBranchDifference:
    label: str
    majority_label: str | None
    member_ids: list[str]
    member_paths: list[str]
    message: str | None

@dataclass(slots=True)
class WorkspaceGitStatus:
    clean: bool
    file_changes: list[GitFileChange]
    branches: list[GitMemberBranchStatus]
    branch_groups: list[GitBranchGroup]
    branch_differences: list[GitBranchDifference]
    root_status: WorkspaceRootGitStatus | None
    root_file_changes: list[WorkspaceRootFileChange]

@dataclass(slots=True)
class PlannedChange:
    action: PlannedAction
    from_ref: str | None
    to_ref: str | None
    message: str | None

@dataclass(slots=True)
class MemberResponse:
    member_id: str
    member_path: str
    source_kind: SourceKind
    status: MemberStatus
    error: GwzError | None
    planned: PlannedChange | None
    state: ResolvedMemberState | None
    git_status: GitStatus | None
    lock_match: LockMatch | None

@dataclass(slots=True)
class ResponseEnvelope:
    meta: ResponseMeta
    members: list[MemberResponse]
    errors: list[GwzError]

@dataclass(slots=True)
class OperationEvent:
    operation_id: str
    request_id: str
    sequence: int
    timestamp_ms: int
    kind: EventKind
    severity: Severity
    member_id: str | None
    member_path: str | None
    message: str | None
    member: MemberResponse | None
    error: GwzError | None
    attribution: OperationAttribution | None
    progress: GitTransferProgress | None

@dataclass(slots=True)
class OperationResult:
    operation_id: str
    request_id: str
    action: ActionKind
    aggregate_status: AggregateStatus
    started_at_ms: int
    finished_at_ms: int
    members: list[MemberResponse]
    errors: list[GwzError]
    attribution: OperationAttribution | None

@dataclass(slots=True)
class CreateWorkspaceRequest:
    meta: RequestMeta
    workspace_root: str
    workspace_id: str | None

@dataclass(slots=True)
class InitFromSourcesRequest:
    meta: RequestMeta
    workspace_root: str
    sources: list[SourceUrl]
    target: MaterializeTarget | None
    workspace_id: str | None

@dataclass(slots=True)
class AddExistingRepoRequest:
    meta: RequestMeta
    repository_path: str
    member_path: str | None
    member_id: str | None
    source_id: str | None

@dataclass(slots=True)
class CreateRepoRequest:
    meta: RequestMeta
    member_path: str
    initial_branch: str | None
    member_id: str | None
    source_id: str | None

@dataclass(slots=True)
class MaterializeRequest:
    meta: RequestMeta
    target: MaterializeTarget

@dataclass(slots=True)
class StatusRequest:
    meta: RequestMeta
    mode: StatusMode | None
    include_file_changes: bool | None
    include_branch_summary: bool | None
    path_style: StatusPathStyle | None

@dataclass(slots=True)
class LsRequest:
    meta: RequestMeta
    include_unmaterialized: bool | None

@dataclass(slots=True)
class MemberEntry:
    id: str
    path: str
    abspath: str
    materialized: bool

@dataclass(slots=True)
class LsResponse:
    response: ResponseEnvelope
    members: list[MemberEntry] | None

@dataclass(slots=True)
class ExecResult:
    id: str
    path: str
    exit_code: int | None
    signal: int | None
    spawn_error: str | None

@dataclass(slots=True)
class ExecRequest:
    meta: RequestMeta
    mode: ExecMode
    command: list[str]
    members: list[MemberEntry]
    continue_on_fail: bool | None

@dataclass(slots=True)
class ExecResponse:
    response: ResponseEnvelope
    results: list[ExecResult] | None

@dataclass(slots=True)
class SnapshotRequest:
    meta: RequestMeta
    snapshot_id: str

@dataclass(slots=True)
class TagRequest:
    meta: RequestMeta
    op: TagOp
    name: str | None
    message: str | None
    signed: bool | None
    remote: str | None
    all: bool | None

@dataclass(slots=True)
class CaptureRequest:
    meta: RequestMeta

@dataclass(slots=True)
class CommitRequest:
    meta: RequestMeta
    message: str
    all: bool | None

@dataclass(slots=True)
class StageRequest:
    meta: RequestMeta
    cwd: str
    pathspecs: list[str]
    all: bool | None

@dataclass(slots=True)
class PullHeadRequest:
    meta: RequestMeta

@dataclass(slots=True)
class PullSnapshotRequest:
    meta: RequestMeta
    snapshot_id: str

@dataclass(slots=True)
class PushRequest:
    meta: RequestMeta
    remote: str | None
    refspec: str | None

@dataclass(slots=True)
class CreateWorkspaceResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class InitFromSourcesResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class AddExistingRepoResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class CreateRepoResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class MaterializeResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class StatusResponse:
    response: ResponseEnvelope
    workspace_git_status: WorkspaceGitStatus | None

@dataclass(slots=True)
class SnapshotResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class TagInfo:
    name: str
    members: int

@dataclass(slots=True)
class TagResponse:
    response: ResponseEnvelope
    tags: list[TagInfo] | None

@dataclass(slots=True)
class CaptureResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class CommitResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class StageResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class PullHeadResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class PullSnapshotResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class PushResponse:
    response: ResponseEnvelope

