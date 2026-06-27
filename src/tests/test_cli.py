from __future__ import annotations

from gwz.cli import build_parser
from gwz.cli_render import render_response
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    ListSnapshotsResponse,
    ResponseEnvelope,
    ResponseMeta,
    SnapshotInfo,
)


def test_cli_parser_accepts_status() -> None:
    args = build_parser().parse_args(["--root", ".", "--json", "status", "--combined"])
    assert args.command == "status"
    assert args.root == "."
    assert args.json is True
    assert args.combined is True


def test_cli_render_snapshot_listing() -> None:
    response = ListSnapshotsResponse(
        response=ResponseEnvelope(
            meta=ResponseMeta(
                request_id="req_test",
                schema_version="gwz.protocol/v0",
                action=ActionKind.list_snapshots,
                aggregate_status=AggregateStatus.ok,
                operation_id="op_test",
                message=None,
                attribution=None,
            ),
            members=[],
            errors=[],
        ),
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
