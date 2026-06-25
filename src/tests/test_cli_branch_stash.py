from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gwz.cli import build_parser
from gwz.cli_shared import CliUsageError, CommandContext, meta_kwargs, validate_args
from gwz.protocol.generated import BranchOp, StashOp


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def branch(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("branch", args, kwargs))
        return "branch"

    async def stash(self, **kwargs: Any) -> str:
        self.calls.append(("stash", (), kwargs))
        return "stash"


def run_handler(argv: list[str], client: FakeClient) -> Any:
    args = build_parser().parse_args(argv)
    validate_args(args)
    context = CommandContext(args=args, client=client, meta=meta_kwargs(args))
    return asyncio.run(args.command_handler(context))


def test_branch_create_from_switch_calls_client() -> None:
    client = FakeClient()

    run_handler(["branch", "--create", "feature", "--from", "main", "--switch"], client)

    assert client.calls[0] == (
        "branch",
        ("feature",),
        {
            "op": BranchOp.create,
            "start_ref": "main",
            "switch_after_create": True,
        },
    )


def test_branch_merge_uses_start_ref() -> None:
    client = FakeClient()

    run_handler(["branch", "--merge", "origin/main"], client)

    assert client.calls[0] == (
        "branch",
        (None,),
        {"op": BranchOp.merge, "start_ref": "origin/main"},
    )


def test_branch_rejects_switch_without_create() -> None:
    client = FakeClient()

    with pytest.raises(CliUsageError, match="--switch requires --create"):
        run_handler(["branch", "--switch"], client)


def test_stash_push_calls_client() -> None:
    client = FakeClient()

    run_handler(["stash", "push", "-u", "-m", "work"], client)

    assert client.calls[0] == (
        "stash",
        (),
        {
            "op": StashOp.push,
            "message": "work",
            "include_untracked": True,
            "include_ignored": None,
        },
    )


def test_stash_drop_requires_id_and_calls_client() -> None:
    client = FakeClient()

    run_handler(["stash", "drop", "stash_1"], client)

    assert client.calls[0] == (
        "stash",
        (),
        {"op": StashOp.drop, "stash_id": "stash_1"},
    )


def test_stash_push_rejects_untracked_and_ignored() -> None:
    client = FakeClient()

    with pytest.raises(CliUsageError, match="-u and -a"):
        run_handler(["stash", "push", "-u", "-a"], client)
