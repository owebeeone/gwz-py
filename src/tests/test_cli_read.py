from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gwz.cli import build_parser
from gwz.cli_shared import CliUsageError, CommandContext, meta_kwargs, validate_args


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
        (["repo", "create", "repos/app"], ("create_repo", ("repos/app",), {})),
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
