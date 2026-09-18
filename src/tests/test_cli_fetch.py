"""`gwz fetch` in the Python driver (gwz-cli dev-docs/GwzFetchPlan.md step 1.6):
the client method encodes the request, the CLI verb carries no options of its
own, and the human report matches the Rust CLI line for line."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gwz.cli import build_parser
from gwz.cli_render import render_response
from gwz.client import Client
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    GwzErrorCode,
    FetchRepoSummary,
    FetchResponse,
    FetchResult,
    GwzError,
    MemberResponse,
    MemberStatus,
    ResponseEnvelope,
    ResponseMeta,
    SourceKind,
    TargetKind,
)

MOVED_FROM = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
MOVED_TO = "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432"


def _row(
    member_id: str,
    member_path: str,
    result: FetchResult,
    **overrides: Any,
) -> FetchRepoSummary:
    fields: dict[str, Any] = {
        "member_id": member_id,
        "member_path": member_path,
        "source_kind": SourceKind.git,
        "result": result,
        "remote": "origin",
        "branch": "main",
        "before": None,
        "after": None,
        "upstream": "refs/remotes/origin/main",
        "ahead": 0,
        "behind": 0,
    }
    fields.update(overrides)
    return FetchRepoSummary(**fields)


def _member(member_id: str, member_path: str, status: MemberStatus, error: GwzError | None = None) -> MemberResponse:
    return MemberResponse(
        member_id=member_id,
        member_path=member_path,
        source_kind=SourceKind.git,
        status=status,
        error=error,
        planned=None,
        state=None,
        git_status=None,
        lock_match=None,
        target_kind=TargetKind.root if member_id == "@root" else TargetKind.member,
        lock_difference_reasons=None,
        url_resolution=None,
    )


def _response(
    aggregate_status: AggregateStatus,
    members: list[MemberResponse],
    repos: list[FetchRepoSummary],
) -> FetchResponse:
    return FetchResponse(
        response=ResponseEnvelope(
            meta=ResponseMeta(
                transport=None,
                request_id="req_fetch",
                schema_version="gwz.protocol/v0",
                action=ActionKind.fetch,
                aggregate_status=aggregate_status,
                operation_id="op_fetch",
                message=None,
                attribution=None,
            ),
            members=members,
            errors=[],
        ),
        repos=repos,
    )


class _RecordingClient(Client):
    """Captures the method name and request the client would have sent."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, Any]] = []

    async def _call(self, method: str, request: Any, response_type: Any) -> Any:
        self.calls.append((method, request))
        return _response(AggregateStatus.noop, [], [])


def test_client_fetch_sends_a_meta_only_request() -> None:
    client = _RecordingClient()
    asyncio.run(client.fetch())
    (method, request), = client.calls
    assert method == "fetch"
    assert type(request).__name__ == "FetchRequest"
    # Plan D4: there is no remote_check to send, and no remote field either --
    # a remote token rides in the meta policy, as it does for pull.
    assert not hasattr(request, "remote_check")
    assert not hasattr(request, "remote")


def test_the_fetch_verb_takes_no_options_of_its_own() -> None:
    parser = build_parser()
    assert parser.parse_args(["fetch"]).command == "fetch"
    for rejected in (["fetch", "--check-remotes"], ["fetch", "--prune"]):
        with pytest.raises(SystemExit):
            parser.parse_args(rejected)


def test_the_human_report_is_one_line_per_repository() -> None:
    rendered = render_response(
        _response(
            AggregateStatus.ok,
            [
                _member("@root", ".", MemberStatus.noop),
                _member("mem_core", "gwz-core", MemberStatus.ok),
                _member("mem_local", "local", MemberStatus.noop),
            ],
            [
                _row("@root", ".", FetchResult.unchanged),
                _row(
                    "mem_core",
                    "gwz-core",
                    FetchResult.updated,
                    before=MOVED_FROM,
                    after=MOVED_TO,
                    behind=3,
                ),
                _row(
                    "mem_local",
                    "local",
                    FetchResult.no_upstream,
                    remote=None,
                    branch=None,
                    upstream=None,
                    ahead=None,
                    behind=None,
                ),
            ],
        )
    )
    lines = rendered.splitlines()
    assert lines[0] == "status: Ok"
    assert len(lines) == 4, rendered
    assert lines[1].startswith("@root")
    assert "no change" in lines[1] and "(origin/main, +0 -0)" in lines[1]
    assert "a1b2c3d..9f8e7d6" in lines[2]
    assert "(origin/main, +0 -3)" in lines[2]
    assert "no upstream" in lines[3]
    assert "(" not in lines[3]
    assert lines[3] == lines[3].rstrip(), "no trailing padding"


def test_a_failed_row_carries_its_reason() -> None:
    error = GwzError(
        code=GwzErrorCode.remote_rejected,
        message="connection refused",
        detail=None,
        member_id="mem_broken",
        member_path="broken",
        target_kind=TargetKind.member,
        record_context=None,
    )
    rendered = render_response(
        _response(
            AggregateStatus.partial,
            [
                _member("mem_good", "good", MemberStatus.noop),
                _member("mem_broken", "broken", MemberStatus.failed, error),
            ],
            [
                _row("mem_good", "good", FetchResult.unchanged),
                _row(
                    "mem_broken",
                    "broken",
                    FetchResult.failed,
                    upstream=None,
                    ahead=None,
                    behind=None,
                ),
            ],
        )
    )
    assert rendered.startswith("status: Partial")
    assert "failed" in rendered
    assert "connection refused" in rendered


def test_a_branch_response_still_renders_as_a_branch_response() -> None:
    """Branch and fetch both carry `repos`; the response kind decides."""

    from gwz.protocol.generated import BranchActionResult, BranchRepoSummary, BranchResponse

    response = BranchResponse(
        response=ResponseEnvelope(
            meta=ResponseMeta(
                transport=None,
                request_id="req_branch",
                schema_version="gwz.protocol/v0",
                action=ActionKind.branch,
                aggregate_status=AggregateStatus.ok,
                operation_id="op_branch",
                message=None,
                attribution=None,
            ),
            members=[],
            errors=[],
        ),
        repos=[
            BranchRepoSummary(
                member_id="mem_app",
                member_path="app",
                source_kind=SourceKind.git,
                result=BranchActionResult.listed,
                branch="main",
                current_branch="main",
                detached=False,
                unborn=False,
                head=None,
                upstream=None,
                ahead=None,
                behind=None,
                source_ref=None,
                target_branch=None,
                resulting_commit=None,
                conflict_paths=[],
            )
        ],
    )
    rendered = render_response(response)
    assert "no change" not in rendered
    assert "no upstream" not in rendered
