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
    repo_sync = 16
    stash = 17
    branch = 18
    clone_workspace = 19
    list_snapshots = 20
    diff = 21
    clone_repo_member = 22
    detach_repo_member = 23
    attach_repo_member = 24
    merge = 25

class TagOp(Enum):
    create = 0
    list = 1
    fetch = 2
    push = 3
    delete = 4

class StashOp(Enum):
    push = 0
    list = 1
    apply = 2
    pop = 3
    drop = 4

class StashParticipation(Enum):
    stashed = 0
    empty = 1
    skipped = 2

class StashPushLifecycle(Enum):
    unattempted = 0
    saving = 1
    saved = 2
    empty = 3
    failed = 4

class StashRestoreState(Enum):
    pending = 0
    applied = 1
    popped = 2
    dropped = 3
    noop = 4
    missing = 5

class BranchOp(Enum):
    list = 0
    create = 1
    delete = 2
    merge = 3

class MergeOp(Enum):
    start = 0
    resume = 1
    abort = 2
    status = 3
    gc = 4

class MergeMode(Enum):
    normal = 0
    ff_only = 1
    no_ff = 2

class MergeAnalysisKind(Enum):
    up_to_date = 0
    fast_forward = 1
    true_merge = 2
    unknown = 3

class MergePendingActionKind(Enum):
    verify_up_to_date = 0
    fast_forward = 1
    true_merge = 2
    resolve_conflict = 3

class MergePendingActionState(Enum):
    not_started = 0
    expected_conflict = 1
    completed_exactly = 2
    ambiguous = 3

class MergeParticipantState(Enum):
    planned = 0
    up_to_date = 1
    fast_forwarded = 2
    merged = 3
    conflicted = 4
    failed = 5
    unattempted = 6
    continued = 7
    aborted = 8
    rolled_back = 9

class MergeOperationState(Enum):
    executing = 0
    awaiting_resolution = 1
    halted = 2
    finalizing = 3
    preserving = 4
    rolling_back = 5
    completed = 6
    aborted = 7
    recovery_required = 8
    idle = 9

class MergeParticipantDriftKind(Enum):
    branch_changed = 0
    head_advanced = 1
    head_rewound = 2
    target_ref_changed = 3
    worktree_modified = 4
    index_modified = 5
    merge_state_missing = 6
    merge_head_changed = 7
    new_integration_state = 8
    repository_missing = 9
    head_diverged = 10
    object_missing = 11
    foreign_integration_state = 12
    pending_action_ambiguous = 13

class MergeOperationDriftKind(Enum):
    baseline_lock_changed = 0
    baseline_manifest_changed = 1
    root_candidate_metadata_invalid = 2
    root_candidate_state_changed = 3
    record_unreadable = 4

class MergePublicationStep(Enum):
    not_started = 0
    validating_results = 1
    preparing_candidate = 2
    committing_evidence = 3
    publishing_candidate = 4
    verifying_publication = 5
    complete = 6

class BranchActionResult(Enum):
    listed = 0
    created = 1
    exists = 2
    deleted = 3
    switched = 4
    noop = 5
    skipped = 6
    merged = 7
    conflicted = 8

class ExecMode(Enum):
    argv = 0
    shell = 1

class SourceKind(Enum):
    git = 0
    archive = 1
    package = 2
    local = 3
    generated = 4

class TargetKind(Enum):
    root = 0
    member = 1

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
    branch = 5

class SnapshotSourceKind(Enum):
    current = 0
    branch = 1

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
    detach_member = 15
    attach_member = 16

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
    operation_state_changed = 7

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
    branch_detached_head = 30
    branch_unborn_head = 31
    branch_mixed = 32
    stash_not_found = 33
    stash_incomplete = 34
    stash_conflict = 35
    source_identity_mismatch = 36
    deprecated_operation = 37
    merge_validation_failed = 38
    merge_id_mismatch = 39
    merge_drift = 40
    open_operation = 41
    merge_recovery_required = 42
    merge_phase_unsupported = 43
    root_merge_not_yet_supported = 44
    merge_record_unreadable = 45

class DiffComparisonKind(Enum):
    worktree_vs_index = 0
    index_vs_tree = 1
    worktree_vs_tree = 2
    tree_vs_tree = 3

class DiffOutputFormat(Enum):
    patch = 0
    raw = 1
    name_only = 2
    name_status = 3
    stat = 4
    numstat = 5
    shortstat = 6
    summary = 7
    patch_with_raw = 8
    patch_with_stat = 9
    no_patch = 10

class DiffManifestMode(Enum):
    full = 0
    any_difference = 1

class DiffAlgorithm(Enum):
    default = 0
    myers = 1
    minimal = 2
    patience = 3

class DiffWhitespaceMode(Enum):
    default = 0
    ignore_all = 1
    ignore_change = 2
    ignore_eol = 3
    ignore_blank_lines = 4

class DiffStatus(Enum):
    added = 0
    modified = 1
    deleted = 2
    renamed = 3
    copied = 4
    type_changed = 5
    unmerged = 6

class DiffChunkEncoding(Enum):
    utf8 = 0
    bytes = 1

class DiffOutputRecordKind(Enum):
    patch_bytes = 0
    file_started = 1
    file_finished = 2
    stale_file = 3
    diagnostic = 4

class DiffTargetExclusionReason(Enum):
    snapshot_missing = 0
    snapshot_missing_commit = 1
    root_not_in_snapshot = 2

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
    targets: list[str]
    exclude_targets: list[str]

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
    target_kind: TargetKind | None

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
class SnapshotSource:
    kind: SnapshotSourceKind
    branch: str | None

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
class StashDirtySummary:
    staged: bool
    unstaged: bool
    untracked: bool
    ignored: bool

@dataclass(slots=True)
class StashErrorDetail:
    code: str
    message: str

@dataclass(slots=True)
class StashWarning:
    code: str
    message: str
    member_id: str | None

@dataclass(slots=True)
class StashDrift:
    code: str
    message: str
    member_id: str

@dataclass(slots=True)
class StashBundleMember:
    member_id: str
    path: str
    participation: StashParticipation
    push_lifecycle: StashPushLifecycle
    restore_state: StashRestoreState
    branch_before: str | None
    head_before: str | None
    full_stash_message: str
    dirty_summary: StashDirtySummary
    native_stash_object_id: str | None
    native_stash_display_ref: str | None
    error: StashErrorDetail | None

@dataclass(slots=True)
class StashBundle:
    schema: str
    workspace_id: str
    stash_id: str
    created_at: str
    message_suffix: str
    include_untracked: bool
    include_ignored: bool
    members: list[StashBundleMember]
    warnings: list[StashWarning]
    drift: list[StashDrift]
    selected_members: list[str]

@dataclass(slots=True)
class BranchRepoSummary:
    member_id: str
    member_path: str
    source_kind: SourceKind
    result: BranchActionResult
    branch: str | None
    current_branch: str | None
    detached: bool
    unborn: bool
    head: str | None
    upstream: str | None
    ahead: int | None
    behind: int | None
    source_ref: str | None
    target_branch: str | None
    resulting_commit: str | None
    conflict_paths: list[str]

@dataclass(slots=True)
class MergeParticipantCounts:
    total: int
    planned: int
    up_to_date: int
    fast_forwarded: int
    merged: int
    conflicted: int
    failed: int
    unattempted: int
    continued: int
    aborted: int
    rolled_back: int

@dataclass(slots=True)
class MergeParticipantDrift:
    kind: MergeParticipantDriftKind
    message: str
    expected_branch: str | None
    live_branch: str | None
    expected_head: str | None
    live_head: str | None
    expected_merge_head: str | None
    live_merge_head: str | None

@dataclass(slots=True)
class MergeOperationDrift:
    kind: MergeOperationDriftKind
    message: str

@dataclass(slots=True)
class MergePreservation:
    target_id: str
    path: str
    backup_ref: str | None
    backup_commit: str | None
    stash_id: str | None
    stash_object_id: str | None

@dataclass(slots=True)
class MergePendingActionSummary:
    kind: MergePendingActionKind
    state: MergePendingActionState
    message: str | None

@dataclass(slots=True)
class MergeRepoSummary:
    target_id: str
    target_kind: TargetKind
    path: str
    source_ref: str
    source_commit: str
    target_branch: str
    before_commit: str
    resulting_commit: str | None
    live_commit: str | None
    state: MergeParticipantState
    predicted: MergeAnalysisKind | None
    prediction_complete: bool | None
    conflict_paths: list[str]
    continue_eligible: bool | None
    abort_eligible: bool | None
    drift: list[MergeParticipantDrift]
    error: GwzError | None
    pending_action: MergePendingActionSummary | None

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
    target_kind: TargetKind | None

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
    target_kind: TargetKind | None
    merge_state: MergeOperationState | None
    merge_member: MergeRepoSummary | None
    artifact_path: str | None

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
class CloneWorkspaceRequest:
    meta: RequestMeta
    url: str
    target: str

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
class RepoSyncRequest:
    meta: RequestMeta

@dataclass(slots=True)
class CloneRepoMemberRequest:
    meta: RequestMeta
    source: SourceUrl
    member_id: str | None
    source_id: str | None

@dataclass(slots=True)
class DetachRepoMemberRequest:
    meta: RequestMeta

@dataclass(slots=True)
class AttachRepoMemberRequest:
    meta: RequestMeta

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
    target_kind: TargetKind | None

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
    source: SnapshotSource | None

@dataclass(slots=True)
class ListSnapshotsRequest:
    meta: RequestMeta

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
    commit_marker: bool | None

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
class StashRequest:
    meta: RequestMeta
    op: StashOp
    stash_id: str | None
    message: str | None
    include_untracked: bool | None
    include_ignored: bool | None
    expanded: bool | None
    preserve_index: bool | None

@dataclass(slots=True)
class BranchRequest:
    meta: RequestMeta
    op: BranchOp
    name: str | None
    start_ref: str | None
    switch_after_create: bool | None

@dataclass(slots=True)
class MergeRequest:
    meta: RequestMeta
    op: MergeOp
    source_ref: str | None
    merge_id: str | None
    mode: MergeMode | None
    message: str | None
    preserve: bool | None

@dataclass(slots=True)
class CreateWorkspaceResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class InitFromSourcesResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class CloneWorkspaceResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class AddExistingRepoResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class CreateRepoResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class RepoSyncResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class CloneRepoMemberResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class DetachRepoMemberResponse:
    response: ResponseEnvelope

@dataclass(slots=True)
class AttachRepoMemberResponse:
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
class SnapshotInfo:
    name: str
    created_at: str
    created_by: str
    members: int

@dataclass(slots=True)
class ListSnapshotsResponse:
    response: ResponseEnvelope
    snapshots: list[SnapshotInfo] | None

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

@dataclass(slots=True)
class StashResponse:
    response: ResponseEnvelope
    bundles: list[StashBundle] | None

@dataclass(slots=True)
class BranchResponse:
    response: ResponseEnvelope
    repos: list[BranchRepoSummary] | None

@dataclass(slots=True)
class MergeResponse:
    response: ResponseEnvelope
    merge_id: str | None
    state: MergeOperationState
    open: bool
    participant_counts: MergeParticipantCounts
    repos: list[MergeRepoSummary]
    operation_drift: list[MergeOperationDrift]
    preservation: list[MergePreservation] | None
    publication_step: MergePublicationStep | None

@dataclass(slots=True)
class DiffComparison:
    kind: DiffComparisonKind
    left: str | None
    right: str | None
    merge_base: bool | None

@dataclass(slots=True)
class DiffOptions:
    output_format: DiffOutputFormat | None
    context_lines: int | None
    interhunk_lines: int | None
    algorithm: DiffAlgorithm | None
    whitespace: DiffWhitespaceMode | None
    find_renames: bool | None
    find_copies: bool | None
    rename_threshold: int | None
    rename_limit: int | None
    binary: bool | None
    text: bool | None
    full_index: bool | None
    abbrev: int | None
    reverse: bool | None
    null_terminated: bool | None
    src_prefix: str | None
    dst_prefix: str | None
    no_prefix: bool | None
    line_prefix: str | None
    ignore_submodules: str | None
    diff_filter: str | None
    manifest_mode: DiffManifestMode | None
    echo_manifest_entries: bool | None

@dataclass(slots=True)
class DiffRequest:
    meta: RequestMeta
    workspace_cwd: str | None
    operands: list[str]
    explicit_pathspecs: list[str]
    options: DiffOptions | None
    cached: bool | None
    merge_base: bool | None

@dataclass(slots=True)
class DiffRepoScope:
    root: bool | None
    member_id: str | None
    member_path: str | None
    source_kind: SourceKind | None

@dataclass(slots=True)
class DiffExcludedTarget:
    scope: DiffRepoScope
    reason: DiffTargetExclusionReason
    snapshot_id: str | None
    message: str | None

@dataclass(slots=True)
class DiffParsedTarget:
    target_id: str
    scope: DiffRepoScope
    comparison: DiffComparison
    pathspecs: list[str]
    left_oid: str | None
    right_oid: str | None
    merge_base_oid: str | None
    left_snapshot_id: str | None
    right_snapshot_id: str | None

@dataclass(slots=True)
class DiffFileEntry:
    file_id: str
    scope: DiffRepoScope
    status: DiffStatus
    old_path: str | None
    new_path: str | None
    old_mode: int | None
    new_mode: int | None
    similarity: int | None
    insertions: int | None
    deletions: int | None
    is_binary: bool | None

@dataclass(slots=True)
class DiffRepoSummary:
    scope: DiffRepoScope
    has_differences: bool
    files_changed: int
    insertions: int
    deletions: int
    files_manifested: int

@dataclass(slots=True)
class DiffSummary:
    has_differences: bool
    repos_examined: int
    repos_with_differences: int
    files_changed: int
    insertions: int
    deletions: int
    repo_summaries: list[DiffRepoSummary]

@dataclass(slots=True)
class DiffOutputLogRef:
    log_id: str
    format: DiffOutputFormat
    encoding: DiffChunkEncoding | None

@dataclass(slots=True)
class DiffManifestResponse:
    response: ResponseEnvelope
    files: list[DiffFileEntry]
    summary: DiffSummary | None
    targets: list[DiffParsedTarget]
    output: DiffOutputLogRef | None
    excluded_targets: list[DiffExcludedTarget]

@dataclass(slots=True)
class DiffOutputRecord:
    kind: DiffOutputRecordKind
    scope: DiffRepoScope | None
    file_id: str | None
    entry: DiffFileEntry | None
    data: bytes | None
    stale: bool | None
    diagnostic: str | None

