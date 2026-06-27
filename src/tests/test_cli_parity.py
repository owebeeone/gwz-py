from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from gwz import cli
from gwz.cli import build_parser
from gwz.cli_shared import CliUsageError, CommandContext, meta_kwargs, validate_args
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    BranchActionResult,
    BranchOp,
    BranchRepoSummary,
    BranchResponse,
    LsResponse,
    MemberEntry,
    ResponseEnvelope,
    ResponseMeta,
    SourceKind,
    StatusResponse,
    TargetKind,
    WorkspaceGitStatus,
    WorkspaceRootGitStatus,
)


FIXTURE = Path(__file__).parent / "fixtures" / "cli_parity" / "parser_cases.json"


class FakeClient:
    root = None

    async def ls(self, **kwargs: Any) -> Any:
        raise AssertionError("semantic parser tests should fail before client calls")


class FakeRenderClient:
    def __init__(self, root: str | None = None) -> None:
        self.root = root

    async def __aenter__(self) -> FakeRenderClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def status(self, **kwargs: Any) -> StatusResponse:
        return _status_response()

    async def ls(self, **kwargs: Any) -> LsResponse:
        return _ls_response()

    async def branch(self, *args: Any, **kwargs: Any) -> BranchResponse:
        assert kwargs.get("op") is BranchOp.list
        return _branch_response()


def cases() -> dict[str, list[list[str]]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _response(action: ActionKind) -> ResponseEnvelope:
    return ResponseEnvelope(
        meta=ResponseMeta(
            request_id="req_parity",
            schema_version="gwz.protocol/v0",
            action=action,
            aggregate_status=AggregateStatus.ok,
            operation_id=None,
            message=None,
            attribution=None,
        ),
        members=[],
        errors=[],
    )


def _status_response() -> StatusResponse:
    return StatusResponse(
        response=_response(ActionKind.status),
        workspace_git_status=WorkspaceGitStatus(
            clean=True,
            file_changes=[],
            branches=[],
            branch_groups=[],
            branch_differences=[],
            root_status=WorkspaceRootGitStatus(
                branch="main",
                detached=False,
                head="1111111111111111111111111111111111111111",
                staged=0,
                unstaged=0,
                untracked=0,
                dirty=False,
                unborn=False,
            ),
            root_file_changes=[],
        ),
    )


def _ls_response() -> LsResponse:
    return LsResponse(
        response=_response(ActionKind.ls),
        members=[
            MemberEntry(
                id="mem_app",
                path="repos/app",
                abspath="/workspace/repos/app",
                materialized=True,
                target_kind=TargetKind.member,
            ),
            MemberEntry(
                id="mem_lib",
                path="libs/lib",
                abspath="/workspace/libs/lib",
                materialized=True,
                target_kind=TargetKind.member,
            ),
        ],
    )


def _branch_response() -> BranchResponse:
    return BranchResponse(
        response=_response(ActionKind.branch),
        repos=[
            BranchRepoSummary(
                member_id="mem_app",
                member_path="repos/app",
                source_kind=SourceKind.git,
                result=BranchActionResult.listed,
                branch="main",
                current_branch="main",
                detached=False,
                unborn=False,
                head="1111111111111111111111111111111111111111",
                upstream="origin/main",
                ahead=0,
                behind=0,
                source_ref=None,
                target_branch=None,
                resulting_commit=None,
                conflict_paths=[],
            )
        ],
    )


def _run_cli(
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> str:
    monkeypatch.setattr(cli, "Client", FakeRenderClient)
    args = build_parser().parse_args(argv)

    exit_code = asyncio.run(cli.run(args))
    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.err == ""
    return captured.out.rstrip("\n")


@pytest.mark.parametrize("argv", cases()["accept"])
def test_cli_parity_accepts_representative_command_shapes(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)

    validate_args(args)
    assert callable(args.command_handler)


@pytest.mark.parametrize("argv", cases()["parse_reject"])
def test_cli_parity_rejects_invalid_command_shapes_at_parse(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(argv)

    assert exc_info.value.code != 0


@pytest.mark.parametrize("argv", cases()["semantic_reject"])
def test_cli_parity_rejects_invalid_command_shapes_semantically(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)

    with pytest.raises(CliUsageError):
        validate_args(args)
        context = CommandContext(args=args, client=FakeClient(), meta=meta_kwargs(args))
        asyncio.run(args.command_handler(context))


@pytest.mark.parametrize("argv", cases()["rust_accept"])
def test_cli_parity_accepts_rust_flags(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)

    validate_args(args)
    assert callable(args.command_handler)


@pytest.mark.parametrize("argv", cases()["rust_version"])
def test_cli_parity_accepts_rust_version_flags(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(argv)

    assert exc_info.value.code == 0


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["status"], "On branch main"),
        (["ls"], "/workspace/repos/app\n/workspace/libs/lib"),
        (["ls", "--local"], "repos/app\nlibs/lib"),
        (
            ["branch"],
            "status: Ok\nmem_app repos/app Listed main 1111111111111111111111111111111111111111",
        ),
    ],
)
def test_cli_parity_renders_high_priority_human_output(
    argv: list[str],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run_cli(argv, monkeypatch, capsys) == expected


def test_cli_parity_snapshot_without_name_lists_snapshots() -> None:
    class SnapshotClient(FakeClient):
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def list_snapshots(self, **kwargs: Any) -> str:
            self.calls.append("list_snapshots")
            return "list_snapshots"

    client = SnapshotClient()
    args = build_parser().parse_args(["snapshot"])
    validate_args(args)
    context = CommandContext(args=args, client=client, meta=meta_kwargs(args))

    assert asyncio.run(args.command_handler(context)) == "list_snapshots"
    assert client.calls == ["list_snapshots"]
