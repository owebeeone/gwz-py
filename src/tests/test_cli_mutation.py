from __future__ import annotations

import argparse
import asyncio
from typing import Any

import pytest

from gwz.cli import build_parser
from gwz.cli_shared import CommandContext, meta_kwargs, validate_args
from gwz.protocol.generated import RemoteCheck, SnapshotSourceKind, TagOp


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
        (["add", "-A", "src/file.py"], "stage", (["src/file.py"],)),
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


def test_commit_marker_flags_lower_to_tristate() -> None:
    client = FakeClient()
    run_handler(["commit", "-m", "message"], client)
    assert client.calls[-1][2]["commit_marker"] is None

    run_handler(["commit", "-m", "message", "--commit-marker"], client)
    assert client.calls[-1][2]["commit_marker"] is True

    run_handler(["commit", "-m", "message", "--no-commit-marker"], client)
    assert client.calls[-1][2]["commit_marker"] is False

    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["commit", "-m", "message", "--commit-marker", "--no-commit-marker"]
        )


def test_tag_push_uses_explicit_remote_without_duplicate_meta() -> None:
    client = FakeClient()

    run_handler(["--remote", "origin", "tag", "--push", "v1"], client)

    assert client.calls[0][1] == ("v1",)
    assert client.calls[0][2]["op"] is TagOp.push
    assert client.calls[0][2]["remote"] == "origin"


@pytest.mark.parametrize("argv", [["snapshot"], ["snapshot", "--list"]])
def test_snapshot_list_calls_client(argv: list[str]) -> None:
    client = FakeClient()

    assert run_handler(argv, client) == "list_snapshots"
    assert client.calls[0][0] == "list_snapshots"


def test_push_check_remotes_asks_core_to_read_every_remote() -> None:
    client = FakeClient()

    run_handler(["push"], client)
    run_handler(["push", "--check-remotes"], client)
    run_handler(["push", "--force", "--check-remotes"], client)

    assert [call[2]["remote_check"] for call in client.calls] == [
        None,
        RemoteCheck.always,
        RemoteCheck.always,
    ]
    # `--force` stays the destructive policy; it never becomes a forced refspec.
    assert client.calls[2][2]["destructive"] is True
    assert "refspec" not in client.calls[2][2]


def test_push_check_remotes_help_uses_the_shared_text() -> None:
    subparsers = next(
        action for action in build_parser()._actions if isinstance(action, argparse._SubParsersAction)
    )
    option = next(
        action
        for action in subparsers.choices["push"]._actions
        if "--check-remotes" in action.option_strings
    )

    assert option.help == (
        "Read every selected remote and every root dependency instead of skipping "
        "repositories that are unchanged since the last fetch or push"
    )
