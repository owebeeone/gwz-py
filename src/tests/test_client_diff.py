"""Client-side `diff` request building and `diff_output` cursor-loop behavior.

These exercise the pure Python client seam with an in-memory fake bridge (no
native extension): that `diff()` lowers options into a structured `DiffRequest`
(first-class `cached`/`merge_base`, not operand tunnels) and returns the
metadata-only `DiffManifestResponse`, and that `diff_output()` drives the
taut-shape cursor loop correctly — advancing on ``data``, resuming on
``expired``, stopping on ``eof``/``closed``, raising on ``failed``, and ending
the reader stream on completion and on cancellation.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gwz import Client
from gwz.bridge import DiffLogRead
from gwz.errors import GwzBridgeError
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    DiffChunkEncoding,
    DiffManifestMode,
    DiffManifestResponse,
    DiffOutputFormat,
    DiffOutputLogRef,
    DiffOutputRecord,
    DiffOutputRecordKind,
    DiffRequest,
    ResponseEnvelope,
    ResponseMeta,
)


def _manifest_response(output: DiffOutputLogRef | None = None) -> DiffManifestResponse:
    return DiffManifestResponse(
        response=ResponseEnvelope(
            meta=ResponseMeta(
                request_id="req_test",
                schema_version="gwz.protocol/v0",
                action=ActionKind.diff,
                aggregate_status=AggregateStatus.ok,
                operation_id=None,
                message=None,
                attribution=None,
            ),
            members=[],
            errors=[],
        ),
        files=[],
        summary=None,
        targets=[],
        output=output,
        excluded_targets=[],
    )


def _record(kind: DiffOutputRecordKind, data: bytes | None = None) -> DiffOutputRecord:
    return DiffOutputRecord(
        kind=kind,
        scope=None,
        file_id=None,
        entry=None,
        data=data,
        stale=None,
        diagnostic=None,
    )


class DiffFakeBridge:
    """A fake bridge that records the `diff` request and replays a scripted
    sequence of `diff_log_read` answers, tracking cursors and end_stream calls."""

    def __init__(self, reads: list[DiffLogRead] | None = None) -> None:
        self.diff_request: DiffRequest | None = None
        self.reads = list(reads or [])
        self.read_calls: list[tuple[str, str, int | None]] = []
        self.ended: list[tuple[str, str]] = []
        self.output_ref: DiffOutputLogRef | None = None

    async def call(self, method: str, request_message: str, response_message: str, request: Any) -> Any:
        assert method == "diff"
        self.diff_request = request
        return _manifest_response(self.output_ref)

    async def diff_log_read(
        self,
        log_id: str,
        stream_id: str,
        *,
        cursor: int | None = None,
        max_records: int | None = None,
        max_bytes: int | None = None,
        timeout_ms: int | None = None,
    ) -> DiffLogRead:
        self.read_calls.append((log_id, stream_id, cursor))
        if not self.reads:
            # Default to EOF once the script is exhausted.
            return DiffLogRead(records=[], next_cursor=cursor or 0, state="eof")
        return self.reads.pop(0)

    async def diff_log_end_stream(self, log_id: str, stream_id: str) -> None:
        self.ended.append((log_id, stream_id))


def test_diff_lowers_options_into_structured_request() -> None:
    bridge = DiffFakeBridge()
    client = Client(root="/tmp/ws", bridge=bridge)

    response = asyncio.run(
        client.diff(
            ["A...B"],
            pathspecs=["gwz-core/src"],
            workspace_cwd="gwz-core",
            cached=True,
            merge_base=True,
            tagged=True,
            output_format=DiffOutputFormat.patch,
            context_lines=5,
            find_renames=True,
        )
    )

    assert isinstance(response, DiffManifestResponse)
    request = bridge.diff_request
    assert isinstance(request, DiffRequest)
    # Parsed flags are first-class request fields, never operand tunnels.
    assert request.cached is True
    assert request.merge_base is True
    assert request.tagged is True
    assert request.operands == ["A...B"]
    assert request.explicit_pathspecs == ["gwz-core/src"]
    assert request.workspace_cwd == "gwz-core"
    assert request.options is not None
    assert request.options.output_format is DiffOutputFormat.patch
    assert request.options.context_lines == 5
    assert request.options.find_renames is True


def test_diff_quiet_uses_any_difference_and_string_enum() -> None:
    bridge = DiffFakeBridge()
    client = Client(root="/tmp/ws", bridge=bridge)

    asyncio.run(
        client.diff(
            output_format="no_patch",
            manifest_mode="any_difference",
        )
    )

    request = bridge.diff_request
    assert request is not None and request.options is not None
    assert request.options.output_format is DiffOutputFormat.no_patch
    assert request.options.manifest_mode is DiffManifestMode.any_difference


def test_diff_output_yields_records_then_stops_on_eof() -> None:
    ref = DiffOutputLogRef(log_id="log-1", format=DiffOutputFormat.patch, encoding=DiffChunkEncoding.bytes)
    reads = [
        DiffLogRead(
            records=[
                _record(DiffOutputRecordKind.file_started),
                _record(DiffOutputRecordKind.patch_bytes, b"@@ -1 +1 @@\n"),
            ],
            next_cursor=2,
            state="data",
        ),
        DiffLogRead(
            records=[_record(DiffOutputRecordKind.file_finished)],
            next_cursor=3,
            state="data",
        ),
        DiffLogRead(records=[], next_cursor=3, state="eof"),
    ]
    bridge = DiffFakeBridge(reads)
    client = Client(root="/tmp/ws", bridge=bridge)

    async def collect() -> list[DiffOutputRecordKind]:
        return [r.kind async for r in client.diff_output(ref)]

    kinds = asyncio.run(collect())
    assert kinds == [
        DiffOutputRecordKind.file_started,
        DiffOutputRecordKind.patch_bytes,
        DiffOutputRecordKind.file_finished,
    ]
    # Cursor advanced across reads (0 -> 2 -> 3), and the reader stream was ended.
    assert [c for _, _, c in bridge.read_calls] == [None, 2, 3]
    assert len(bridge.ended) == 1
    assert bridge.ended[0][0] == "log-1"


def test_diff_output_resumes_from_next_cursor_on_expired() -> None:
    ref = DiffOutputLogRef(log_id="log-2", format=DiffOutputFormat.patch, encoding=None)
    reads = [
        # Cursor was evicted below; the engine hands back the earliest resumable.
        DiffLogRead(records=[], next_cursor=5, state="expired"),
        DiffLogRead(
            records=[_record(DiffOutputRecordKind.patch_bytes, b"x")],
            next_cursor=6,
            state="data",
        ),
        DiffLogRead(records=[], next_cursor=6, state="eof"),
    ]
    bridge = DiffFakeBridge(reads)
    client = Client(root="/tmp/ws", bridge=bridge)

    async def collect() -> list[bytes | None]:
        return [r.data async for r in client.diff_output(ref)]

    data = asyncio.run(collect())
    assert data == [b"x"]
    # After expired, the loop re-read from the returned cursor (5), then 6.
    assert [c for _, _, c in bridge.read_calls] == [None, 5, 6]


def test_diff_output_raises_on_failed_but_still_ends_stream() -> None:
    ref = DiffOutputLogRef(log_id="log-3", format=DiffOutputFormat.patch, encoding=None)
    reads = [DiffLogRead(records=[], next_cursor=0, state="failed")]
    bridge = DiffFakeBridge(reads)
    client = Client(root="/tmp/ws", bridge=bridge)

    async def drain() -> None:
        async for _ in client.diff_output(ref):
            pass

    with pytest.raises(GwzBridgeError):
        asyncio.run(drain())
    assert bridge.ended == [("log-3", bridge.ended[0][1])]


def test_diff_output_surfaces_stale_file_record() -> None:
    """A `stale_file` worktree-race record decodes and is surfaced intact (its
    `stale` flag and diagnostic preserved) — never dropped or degraded."""
    ref = DiffOutputLogRef(log_id="log-5", format=DiffOutputFormat.patch, encoding=None)
    stale = DiffOutputRecord(
        kind=DiffOutputRecordKind.stale_file,
        scope=None,
        file_id="mem_app#0",
        entry=None,
        data=None,
        stale=True,
        diagnostic="worktree changed: 'race.txt' no longer differs",
    )
    reads = [
        DiffLogRead(records=[_record(DiffOutputRecordKind.file_started)], next_cursor=1, state="data"),
        DiffLogRead(records=[stale], next_cursor=2, state="data"),
        DiffLogRead(records=[_record(DiffOutputRecordKind.file_finished)], next_cursor=3, state="data"),
        DiffLogRead(records=[], next_cursor=3, state="eof"),
    ]
    bridge = DiffFakeBridge(reads)
    client = Client(root="/tmp/ws", bridge=bridge)

    async def collect() -> list[DiffOutputRecord]:
        return [r async for r in client.diff_output(ref)]

    records = asyncio.run(collect())
    kinds = [r.kind for r in records]
    assert DiffOutputRecordKind.stale_file in kinds
    assert DiffOutputRecordKind.patch_bytes not in kinds
    surfaced = next(r for r in records if r.kind is DiffOutputRecordKind.stale_file)
    assert surfaced.stale is True
    assert surfaced.diagnostic
    assert surfaced.file_id == "mem_app#0"


def test_diff_output_cancellation_ends_stream() -> None:
    ref = DiffOutputLogRef(log_id="log-4", format=DiffOutputFormat.patch, encoding=None)
    reads = [
        DiffLogRead(
            records=[_record(DiffOutputRecordKind.patch_bytes, b"a")],
            next_cursor=1,
            state="data",
        ),
        DiffLogRead(
            records=[_record(DiffOutputRecordKind.patch_bytes, b"b")],
            next_cursor=2,
            state="data",
        ),
        DiffLogRead(records=[], next_cursor=2, state="eof"),
    ]
    bridge = DiffFakeBridge(reads)
    client = Client(root="/tmp/ws", bridge=bridge)

    async def partial() -> None:
        gen = client.diff_output(ref)
        first = await gen.__anext__()
        assert first.data == b"a"
        # Abandon mid-stream: aclose() must end the reader stream (D4/D6).
        await gen.aclose()

    asyncio.run(partial())
    assert bridge.ended == [("log-4", bridge.ended[0][1])]
