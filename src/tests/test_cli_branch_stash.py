from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gwz.cli import build_parser
from gwz.cli_render import render_response
from gwz.cli_shared import CliUsageError, CommandContext, meta_kwargs, validate_args
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    BranchOp,
    GwzError,
    GwzErrorCode,
    MemberResponse,
    MemberStatus,
    ResponseEnvelope,
    ResponseMeta,
    SourceKind,
    StashOp,
    StashResponse,
    TargetKind,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def branch(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("branch", args, kwargs))
        return "branch"

    async def merge(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append(("merge", args, kwargs))
        return "merge"

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


def test_branch_merge_alias_uses_first_class_merge() -> None:
    client = FakeClient()

    run_handler(["branch", "--merge", "origin/main"], client)

    assert client.calls[0] == (
        "merge",
        ("origin/main",),
        {},
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


def test_a_partial_stash_report_prints_no_copied_member_error() -> None:
    """A partial result repeats each failed member's error in `errors` (gwz-cli
    docs/MachineOutput.md, "Partial results"). The stash report shows member
    statuses only, and like the Rust CLI's it prints no copy."""
    error = GwzError(
        code=GwzErrorCode.git_command_failed, message="stash failed", detail=None,
        member_id="mem_lib", member_path="lib", target_kind=TargetKind.member, record_context=None,
    )

    def member(member_id: str, status: MemberStatus, error: GwzError | None) -> MemberResponse:
        return MemberResponse(
            member_id=member_id, member_path=member_id[4:], source_kind=SourceKind.git, status=status,
            error=error, planned=None, state=None, git_status=None, lock_match=None,
            target_kind=TargetKind.member, lock_difference_reasons=None, url_resolution=None,
        )

    def stash(errors: list[GwzError]) -> StashResponse:
        meta = ResponseMeta(
            request_id="req_stash", schema_version="gwz.protocol/v0", action=ActionKind.stash,
            aggregate_status=AggregateStatus.partial, operation_id="op_stash", message=None,
            attribution=None, transport=None,
        )
        members = [member("mem_app", MemberStatus.ok, None), member("mem_lib", MemberStatus.failed, error)]
        return StashResponse(response=ResponseEnvelope(meta=meta, members=members, errors=errors), bundles=[])

    assert render_response(stash([error])) == render_response(stash([]))
