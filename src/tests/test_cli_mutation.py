from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gwz.cli import build_parser
from gwz.cli_shared import CommandContext, meta_kwargs, validate_args
from gwz.protocol.generated import SnapshotSourceKind, TagOp


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def materialize(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("materialize", args, kwargs))
        return "materialize"

    async def snapshot(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("snapshot", args, kwargs))
        return "snapshot"

    async def list_snapshots(self, **kwargs: Any) -> str:
        self.calls.append(("list_snapshots", (), kwargs))
        return "list_snapshots"

    async def tag(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("tag", args, kwargs))
        return "tag"

    async def capture(self, **kwargs: Any) -> str:
        self.calls.append(("capture", (), kwargs))
        return "capture"

    async def stage(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("stage", args, kwargs))
        return "stage"

    async def commit(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("commit", args, kwargs))
        return "commit"

    async def pull_head(self, **kwargs: Any) -> str:
        self.calls.append(("pull_head", (), kwargs))
        return "pull_head"

    async def pull_snapshot(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("pull_snapshot", args, kwargs))
        return "pull_snapshot"

    async def push(self, **kwargs: Any) -> str:
        self.calls.append(("push", (), kwargs))
        return "push"


def run_handler(argv: list[str], client: FakeClient) -> Any:
    args = build_parser().parse_args(argv)
    validate_args(args)
    context = CommandContext(args=args, client=client, meta=meta_kwargs(args))
    return asyncio.run(args.command_handler(context))


@pytest.mark.parametrize(
    "argv,expected_name,expected_args",
    [
        (["materialize", "--switch", "feature"], "materialize", ("branch",)),
        (["capture"], "capture", ()),
        (["stage", "-A", "src/file.py"], "stage", (["src/file.py"],)),
        (["add", "src/file.py"], "stage", (["src/file.py"],)),
        (["commit", "-m", "message", "-a"], "commit", ("message",)),
        (["pull"], "pull_head", ()),
        (["pull", "--snapshot", "snap_1"], "pull_snapshot", ("snap_1",)),
        (["push"], "push", ()),
    ],
)
def test_mutation_handlers_call_client(
    argv: list[str],
    expected_name: str,
    expected_args: tuple[Any, ...],
) -> None:
    client = FakeClient()

    assert run_handler(argv, client) == expected_name

    assert client.calls[0][0] == expected_name
    assert client.calls[0][1] == expected_args


def test_snapshot_branch_builds_source() -> None:
    client = FakeClient()

    run_handler(["snapshot", "snap_1", "--branch", "main"], client)

    source = client.calls[0][2]["source"]
    assert source.kind is SnapshotSourceKind.branch
    assert source.branch == "main"


def test_snapshot_bare_branch_uses_current_branch_source() -> None:
    client = FakeClient()

    run_handler(["snapshot", "snap_1", "--branch"], client)

    source = client.calls[0][2]["source"]
    assert source.kind is SnapshotSourceKind.current
    assert source.branch is None


def test_tag_push_uses_explicit_remote_without_duplicate_meta() -> None:
    client = FakeClient()

    run_handler(["--remote", "origin", "tag", "--push", "v1"], client)

    assert client.calls[0][1] == ("v1",)
    assert client.calls[0][2]["op"] is TagOp.push
    assert client.calls[0][2]["remote"] == "origin"


def test_snapshot_list_calls_client() -> None:
    client = FakeClient()

    assert run_handler(["snapshot", "--list"], client) == "list_snapshots"
    assert client.calls[0][0] == "list_snapshots"
