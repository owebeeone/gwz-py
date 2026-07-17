from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gwz.cli import build_parser
from gwz.cli_shared import CliUsageError, CommandContext, meta_kwargs, validate_args
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    CloneRepoMemberResponse,
    EventKind,
    OperationEvent,
    OperationResult,
    Severity,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def status(self, **kwargs: Any) -> str:
        self.calls.append(("status", (), kwargs))
        return "status"

    async def ls(self, **kwargs: Any) -> str:
        self.calls.append(("ls", (), kwargs))
        return "ls"

    async def create_workspace(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("create_workspace", args, kwargs))
        return "create_workspace"

    async def init_from_sources(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("init_from_sources", args, kwargs))
        return "init_from_sources"

    async def add_existing_repo(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("add_existing_repo", args, kwargs))
        return "add_existing_repo"

    async def clone_repo_member(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("clone_repo_member", args, kwargs))
        return "clone_repo_member"

    async def detach_repo_member(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("detach_repo_member", args, kwargs))
        return "detach_repo_member"

    async def attach_repo_member(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("attach_repo_member", args, kwargs))
        return "attach_repo_member"

    async def create_repo(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("create_repo", args, kwargs))
        return "create_repo"

    async def repo_sync(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("repo_sync", args, kwargs))
        return "repo_sync"


def run_handler(argv: list[str], client: FakeClient) -> Any:
    args = build_parser().parse_args(argv)
    validate_args(args)
    context = CommandContext(args=args, client=client, meta=meta_kwargs(args))
    return asyncio.run(args.command_handler(context))


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["status", "--no-combined", "--no-files"], ("status", (), {"combined": False, "include_file_changes": False, "include_branch_summary": None})),
        (["ls", "--materialized-only"], ("ls", (), {"include_unmaterialized": False})),
        (["init"], ("create_workspace", (None,), {})),
        (["init", "--path", "repos", "https://example.invalid/repo.git"], ("init_from_sources", (["https://example.invalid/repo.git"],), {"workspace_root": "repos"})),
        (["repo", "add", "../repo"], ("add_existing_repo", ("../repo",), {})),
        (["repo", "add", "../repo", "--member-id", "mem_app", "--source-id", "src_app"], ("add_existing_repo", ("../repo",), {"member_id": "mem_app", "source_id": "src_app"})),
        (["repo", "create", "repos/app", "--member-id", "mem_app", "--source-id", "src_app"], ("create_repo", ("repos/app",), {"member_id": "mem_app", "source_id": "src_app"})),
        (["--dry-run", "repo", "clone", "https://example.invalid/shared.git", "libs/shared", "--member-id", "mem_shared", "--source-id", "src_shared"], ("clone_repo_member", ("https://example.invalid/shared.git", "libs/shared"), {"member_id": "mem_shared", "source_id": "src_shared", "dry_run": True})),
        (["--json", "repo", "clone", "https://example.invalid/shared.git", "libs/shared"], ("clone_repo_member", ("https://example.invalid/shared.git", "libs/shared"), {"member_id": None, "source_id": None})),
        (["--dry-run", "repo", "clone", "https://example.invalid/shared.git"], ("clone_repo_member", ("https://example.invalid/shared.git", None), {"member_id": None, "source_id": None, "dry_run": True})),
        (["repo", "detach", "libs/shared"], ("detach_repo_member", ("libs/shared",), {})),
        (["repo", "attach", "mem_shared"], ("attach_repo_member", ("mem_shared",), {})),
        (["repo", "sync", "repos/app"], ("repo_sync", ("repos/app",), {})),
    ],
)
def test_read_handlers_call_client(argv: list[str], expected: tuple[str, tuple[Any, ...], dict[str, Any]]) -> None:
    client = FakeClient()

    assert run_handler(argv, client) == expected[0]

    name, args, kwargs = expected
    assert client.calls[0][0] == name
    assert client.calls[0][1] == args
    assert client.calls[0][2] | kwargs == client.calls[0][2]


def test_repo_sync_member_path_rejects_global_selection() -> None:
    client = FakeClient()

    with pytest.raises(CliUsageError, match="repo sync member path"):
        run_handler(["--member", "mem_app", "repo", "sync", "repos/app"], client)


@pytest.mark.parametrize(
    "argv",
    [
        ["--member", "mem_other", "repo", "detach", "mem_shared"],
        ["--no-target", "@root", "repo", "detach", "mem_shared"],
        ["--all", "repo", "attach", "mem_shared"],
        ["--member-path", "libs/other", "repo", "attach", "mem_shared"],
    ],
)
def test_repo_detach_and_attach_reject_global_selection(argv: list[str]) -> None:
    with pytest.raises(CliUsageError, match="cannot be combined with global selection"):
        run_handler(argv, FakeClient())


def test_repo_attach_rejects_path_operand() -> None:
    with pytest.raises(CliUsageError, match="member id"):
        run_handler(["repo", "attach", "libs/shared"], FakeClient())


def test_repo_clone_human_output_uses_stream_route() -> None:
    class StreamingClient(FakeClient):
        def clone_repo_member_stream(self, *args: Any, **kwargs: Any) -> Any:
            self.calls.append(("clone_repo_member_stream", args, kwargs))

            async def events() -> Any:
                yield OperationEvent(
                    operation_id="op_clone_member",
                    request_id="req_clone_member",
                    sequence=0,
                    timestamp_ms=0,
                    kind=EventKind.operation_started,
                    severity=Severity.info,
                    member_id=None,
                    member_path=None,
                    message=None,
                    member=None,
                    error=None,
                    attribution=None,
                    progress=None,
                    target_kind=None,
                    merge_state=None,
                )

            return events()

        async def operation_result(self, operation_id: str) -> OperationResult:
            self.calls.append(("operation_result", (operation_id,), {}))
            return OperationResult(
                operation_id=operation_id,
                request_id="req_clone_member",
                action=ActionKind.clone_repo_member,
                aggregate_status=AggregateStatus.ok,
                started_at_ms=0,
                finished_at_ms=1,
                members=[],
                errors=[],
                attribution=None,
            )

    client = StreamingClient()

    response = run_handler(
        ["repo", "clone", "https://example.invalid/shared.git", "libs/shared"],
        client,
    )

    assert isinstance(response, CloneRepoMemberResponse)
    assert [call[0] for call in client.calls] == [
        "clone_repo_member_stream",
        "operation_result",
    ]
