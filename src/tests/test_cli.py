from __future__ import annotations

import asyncio

import gwz.cli as cli_module
from gwz.cli import build_parser
from gwz.cli_render import render_response
from gwz.errors import GwzOperationError
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    BranchActionResult,
    BranchRepoSummary,
    BranchResponse,
    ListSnapshotsResponse,
    LsResponse,
    MemberEntry,
    ResponseEnvelope,
    ResponseMeta,
    SnapshotInfo,
    SourceKind,
    StashBundle,
    StashBundleMember,
    StashDirtySummary,
    StashParticipation,
    StashPushLifecycle,
    StashResponse,
    StashRestoreState,
    StatusResponse,
    WorkspaceGitStatus,
    WorkspaceRootFileChange,
    WorkspaceRootGitStatus,
)


def response_envelope(
    action: ActionKind,
    aggregate_status: AggregateStatus = AggregateStatus.ok,
) -> ResponseEnvelope:
    return ResponseEnvelope(
        meta=ResponseMeta(
            request_id="req_test",
            schema_version="gwz.protocol/v0",
            action=action,
            aggregate_status=aggregate_status,
            operation_id="op_test",
            message=None,
            attribution=None,
        ),
        members=[],
        errors=[],
    )


def test_cli_parser_accepts_status() -> None:
    args = build_parser().parse_args(["--root", ".", "--json", "status", "--combined"])
    assert args.command == "status"
    assert args.root == "."
    assert args.json is True
    assert args.combined is True


def test_cli_render_snapshot_listing() -> None:
    response = ListSnapshotsResponse(
        response=response_envelope(ActionKind.list_snapshots),
        snapshots=[
            SnapshotInfo(
                name="snap_one",
                created_at="2026-06-28T00:00:00Z",
                created_by="tester",
                members=2,
            )
        ],
    )

    assert render_response(response) == (
        "1 snapshot:\n  snap_one\t2026-06-28T00:00:00Z\ttester\t(2 members)"
    )


def test_cli_render_status_uses_workspace_branch_summary() -> None:
    response = StatusResponse(
        response=response_envelope(ActionKind.status),
        workspace_git_status=WorkspaceGitStatus(
            clean=True,
            file_changes=[],
            branches=[],
            branch_groups=[],
            branch_differences=[],
            root_status=WorkspaceRootGitStatus(
                branch="main",
                detached=False,
                head="abcdef123456",
                staged=0,
                unstaged=0,
                untracked=0,
                dirty=False,
                unborn=False,
            ),
            root_file_changes=[],
        ),
    )

    assert render_response(response) == "On branch main"


def test_cli_render_status_porcelain() -> None:
    response = StatusResponse(
        response=response_envelope(ActionKind.status),
        workspace_git_status=WorkspaceGitStatus(
            clean=False,
            file_changes=[],
            branches=[],
            branch_groups=[],
            branch_differences=[],
            root_status=WorkspaceRootGitStatus(
                branch="main",
                detached=False,
                head="abcdef123456",
                staged=0,
                unstaged=1,
                untracked=0,
                dirty=True,
                unborn=False,
            ),
            root_file_changes=[
                WorkspaceRootFileChange(
                    repo_path="README.md",
                    workspace_path="README.md",
                    index_status=" ",
                    worktree_status="M",
                    original_repo_path=None,
                )
            ],
        ),
    )

    assert render_response(response, porcelain=True) == " M README.md"


def test_cli_render_ls_paths() -> None:
    response = LsResponse(
        response=response_envelope(ActionKind.ls),
        members=[
            MemberEntry(
                id="mem_app",
                path="repos/app",
                abspath="/workspace/repos/app",
                materialized=True,
                target_kind=None,
            ),
            MemberEntry(
                id="mem_lib",
                path="repos/lib",
                abspath="/workspace/repos/lib",
                materialized=True,
                target_kind=None,
            ),
        ],
    )

    assert render_response(response) == "/workspace/repos/app\n/workspace/repos/lib"
    assert render_response(response, local_paths=True) == "repos/app\nrepos/lib"


def test_cli_render_branch_listing() -> None:
    response = BranchResponse(
        response=response_envelope(ActionKind.branch),
        repos=[
            BranchRepoSummary(
                member_id="mem_gwz_cli",
                member_path="gwz-cli",
                source_kind=SourceKind.git,
                result=BranchActionResult.listed,
                branch="main",
                current_branch="main",
                detached=False,
                unborn=False,
                head="abc123",
                upstream=None,
                ahead=0,
                behind=0,
                source_ref=None,
                target_branch=None,
                resulting_commit=None,
                conflict_paths=[],
            )
        ],
    )

    assert render_response(response) == "status: Ok\nmem_gwz_cli gwz-cli Listed main abc123"


def test_cli_render_stash_list_empty() -> None:
    response = StashResponse(
        response=response_envelope(ActionKind.stash),
        bundles=[],
    )

    assert render_response(response) == "status: Ok"


def test_cli_render_stash_list_bundle_summary() -> None:
    response = StashResponse(
        response=response_envelope(ActionKind.stash),
        bundles=[
            StashBundle(
                schema="gwz.stash/v0",
                workspace_id="ws_test",
                stash_id="stash_apply",
                created_at="2026-01-01T00:00:00Z",
                message_suffix="apply",
                include_untracked=False,
                include_ignored=False,
                members=[
                    StashBundleMember(
                        member_id="mem_app",
                        path="repos/app",
                        participation=StashParticipation.stashed,
                        push_lifecycle=StashPushLifecycle.saved,
                        restore_state=StashRestoreState.pending,
                        branch_before="main",
                        head_before="abc123",
                        full_stash_message="GWZ stash: apply",
                        dirty_summary=StashDirtySummary(
                            staged=False,
                            unstaged=True,
                            untracked=False,
                            ignored=False,
                        ),
                        native_stash_object_id="stash-object",
                        native_stash_display_ref="stash@{0}",
                        error=None,
                    )
                ],
                warnings=[],
                drift=[],
                selected_members=["mem_app"],
            )
        ],
    )

    assert render_response(response) == (
        "status: Ok\nstash_apply 2026-01-01T00:00:00Z (1 member)"
    )


def test_cli_run_renders_dirty_status_response(monkeypatch, capsys) -> None:
    response = StatusResponse(
        response=response_envelope(ActionKind.status, AggregateStatus.dirty),
        workspace_git_status=WorkspaceGitStatus(
            clean=False,
            file_changes=[],
            branches=[],
            branch_groups=[],
            branch_differences=[],
            root_status=WorkspaceRootGitStatus(
                branch="main",
                detached=False,
                head="abcdef123456",
                staged=0,
                unstaged=1,
                untracked=0,
                dirty=True,
                unborn=False,
            ),
            root_file_changes=[
                WorkspaceRootFileChange(
                    repo_path="README.md",
                    workspace_path="README.md",
                    index_status=" ",
                    worktree_status="M",
                    original_repo_path=None,
                )
            ],
        ),
    )

    class DirtyStatusClient:
        def __init__(self, root=None) -> None:
            self.root = root

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info) -> None:
            return None

        async def status(self, **kwargs):
            raise GwzOperationError("dirty status", response=response)

    monkeypatch.setattr(cli_module, "Client", DirtyStatusClient)
    args = build_parser().parse_args(["status"])

    assert asyncio.run(cli_module.run(args)) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.rstrip("\n") == (
        "On branch main\n\nChanges not staged for commit:\n   M README.md"
    )
