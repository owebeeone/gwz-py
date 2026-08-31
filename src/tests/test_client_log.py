"""S3.5 client-side log lowering and output lifecycle acceptance."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gwz import Client
from gwz.bridge import LogOutputRead, NativeCoreBridge
from gwz.errors import GwzBridgeError
from gwz.protocol.codec import encode_message
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    GitObjectIdentity,
    LogDegradation,
    LogDegradationReason,
    LogEntry,
    LogEntryMember,
    LogMergeKind,
    LogMergeProvenance,
    LogOptions,
    LogOutputLogRef,
    LogOutputRecord,
    LogOutputRecordKind,
    LogRequest,
    LogResponse,
    ResponseEnvelope,
    ResponseMeta,
    SourceKind,
)


def _response(status: AggregateStatus = AggregateStatus.ok) -> LogResponse:
    return LogResponse(
        response=ResponseEnvelope(
            meta=ResponseMeta(
                request_id="req_log",
                schema_version="gwz.protocol/v0",
                action=ActionKind.log,
                aggregate_status=status,
                operation_id="op_log",
                message=None,
                attribution=None,
            ),
            members=[],
            errors=[],
        ),
        output=LogOutputLogRef(log_id="commitlog_1"),
    )


def _entry_record() -> LogOutputRecord:
    return LogOutputRecord(
        kind=LogOutputRecordKind.entry,
        entry=LogEntry(
            members=[],
            provenance=LogMergeProvenance(
                kind=LogMergeKind.none,
                gwz_commit_id="marker-invalid",
            ),
            author=None,  # type: ignore[arg-type]
            committer=None,  # type: ignore[arg-type]
            subject="subject",
            body=None,
            ordering_timestamp_ms=1_000,
            author_timestamp_seconds=1,
            committer_timestamp_seconds=1,
            ordering_timestamp_seconds=1,
            lossy=None,
        ),
        degradation=None,
    )


def _degradation_record() -> LogOutputRecord:
    return LogOutputRecord(
        kind=LogOutputRecordKind.degradation,
        entry=None,
        degradation=LogDegradation(
            member_id="mem_missing",
            member_path="repos/missing",
            source_kind=SourceKind.git,
            reason=LogDegradationReason.repository_unreadable,
            operand="HEAD",
            message="cannot read history",
        ),
    )


def _rich_entry_record(index: int, *, marker_invalid: bool = False) -> LogOutputRecord:
    suffix = f"{index:02x}"
    first_hash = ("a" * 38) + suffix
    second_hash = ("b" * 38) + suffix
    return LogOutputRecord(
        kind=LogOutputRecordKind.entry,
        entry=LogEntry(
            members=[
                LogEntryMember(
                    member_id="@root",
                    member_path=".",
                    source_kind=SourceKind.git,
                    commit=first_hash,
                    parents=["1" * 40, "2" * 40],
                ),
                LogEntryMember(
                    member_id=f"mem_{index}",
                    member_path=f"repos/member-{index}",
                    source_kind=SourceKind.git,
                    commit=second_hash,
                    parents=["3" * 40, "4" * 40],
                ),
            ],
            provenance=LogMergeProvenance(
                kind=LogMergeKind.none if marker_invalid else LogMergeKind.marker,
                gwz_commit_id=(
                    "marker-invalid"
                    if marker_invalid
                    else f"01987b0c-2f75-7c4a-9a32-8fd22f7d7c{index:02x}"
                ),
            ),
            author=GitObjectIdentity(
                name=f"Author {index}",
                email=f"author-{index}@example.test",
                time_ms=1_000_000 + index,
                timezone_offset_minutes=330,
            ),
            committer=GitObjectIdentity(
                name=f"Committer {index}",
                email=f"committer-{index}@example.test",
                time_ms=2_000_000 + index,
                timezone_offset_minutes=-60,
            ),
            subject=f"subject {index}",
            body=f"body {index}\nsecond line",
            ordering_timestamp_ms=3_000_000 + index,
            author_timestamp_seconds=1_000 + index,
            committer_timestamp_seconds=2_000 + index,
            ordering_timestamp_seconds=3_000 + index,
            lossy=index % 2 == 1,
        ),
        degradation=None,
    )


def _rich_degradation_record(member_id: str, reason: LogDegradationReason) -> LogOutputRecord:
    return LogOutputRecord(
        kind=LogOutputRecordKind.degradation,
        entry=None,
        degradation=LogDegradation(
            member_id=member_id,
            member_path="." if member_id == "@root" else f"repos/{member_id}",
            source_kind=SourceKind.git,
            reason=reason,
            operand="main..topic",
            message=f"degraded {member_id}",
        ),
    )


class LogBridge:
    def __init__(self, response: LogResponse | None = None) -> None:
        self.response = response or _response()
        self.request: LogRequest | None = None
        self.reads: list[tuple[str, int | None, int | None]] = []
        self.releases: list[str] = []
        self.answers = [
            LogOutputRead([_entry_record(), _degradation_record()], 41, "data"),
            LogOutputRead([], 41, "eof"),
        ]

    async def call(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request: Any,
    ) -> LogResponse:
        assert (method, request_message, response_message) == (
            "log",
            "LogRequest",
            "LogResponse",
        )
        self.request = request
        return self.response

    async def log_output_read(
        self,
        log_id: str,
        *,
        cursor: int | None = None,
        max_records: int | None = None,
    ) -> LogOutputRead:
        self.reads.append((log_id, cursor, max_records))
        return self.answers.pop(0)

    async def log_output_release(self, log_id: str) -> None:
        self.releases.append(log_id)


def test_log_lowers_exact_request_and_preserves_absent_tri_states() -> None:
    bridge = LogBridge()
    client = Client(root="/workspace", bridge=bridge)  # type: ignore[arg-type]

    response = asyncio.run(
        client.log(
            ["HEAD~2..HEAD", "+release.one"],
            pathspecs=[":(exclude)artifact", "src"],
            workspace_cwd="repos/app",
            targets=["@root", "mem_app"],
            concurrency=7,
        )
    )

    assert response is bridge.response
    request = bridge.request
    assert isinstance(request, LogRequest)
    assert request.operands == ["HEAD~2..HEAD", "+release.one"]
    assert request.explicit_pathspecs == [":(exclude)artifact", "src"]
    assert request.workspace_cwd == "repos/app"
    assert request.tagged is None
    assert request.meta.selection is not None
    assert request.meta.selection.targets == ["@root", "mem_app"]
    assert request.meta.policy is not None
    assert request.meta.policy.concurrency == 7
    assert request.options == LogOptions(
        max_entries=None,
        since=None,
        until=None,
        author=None,
        grep=None,
        no_merges=None,
        first_parent=None,
        strict=None,
        coalesce=None,
        include_body=None,
    )


def test_log_lowers_every_explicit_option_without_client_side_parsing() -> None:
    bridge = LogBridge()
    client = Client(bridge=bridge)  # type: ignore[arg-type]

    asyncio.run(
        client.log(
            max_entries=0,
            since="yesterday",
            until="@-1",
            author="[",
            grep="(?-u:\\xFF)",
            no_merges=True,
            first_parent=True,
            strict=True,
            coalesce=False,
            include_body=True,
            tagged=True,
        )
    )

    assert bridge.request is not None
    assert bridge.request.options == LogOptions(
        max_entries=0,
        since="yesterday",
        until="@-1",
        author="[",
        grep="(?-u:\\xFF)",
        no_merges=True,
        first_parent=True,
        strict=True,
        coalesce=False,
        include_body=True,
    )
    assert bridge.request.tagged is True


def test_log_returns_partial_response_instead_of_hiding_its_output() -> None:
    bridge = LogBridge(_response(AggregateStatus.partial))
    client = Client(bridge=bridge)  # type: ignore[arg-type]

    response = asyncio.run(client.log())

    assert response.response.meta.aggregate_status is AggregateStatus.partial
    assert response.output.log_id == "commitlog_1"


def test_log_preserves_exact_failed_response_and_keeps_output_consumable() -> None:
    bridge = LogBridge(_response(AggregateStatus.failed))
    client = Client(bridge=bridge)  # type: ignore[arg-type]

    async def run() -> tuple[LogResponse, list[LogOutputRecord]]:
        response = await client.log(strict=True)
        records = [record async for record in client.log_output(response.output)]
        return response, records

    response, records = asyncio.run(run())

    assert response is bridge.response
    assert response.response.meta.aggregate_status is AggregateStatus.failed
    assert [record.kind for record in records] == [
        LogOutputRecordKind.entry,
        LogOutputRecordKind.degradation,
    ]
    assert bridge.request is not None
    assert bridge.request.options is not None
    assert bridge.request.options.strict is True
    assert bridge.releases == ["commitlog_1"]


def test_log_output_yields_complete_records_then_releases_at_eof() -> None:
    bridge = LogBridge()
    client = Client(bridge=bridge)  # type: ignore[arg-type]

    async def collect() -> list[LogOutputRecord]:
        return [record async for record in client.log_output("commitlog_1")]

    records = asyncio.run(collect())

    assert [record.kind for record in records] == [
        LogOutputRecordKind.entry,
        LogOutputRecordKind.degradation,
    ]
    assert records[0].entry is not None
    assert records[0].entry.provenance.kind is LogMergeKind.none
    assert records[0].entry.provenance.gwz_commit_id == "marker-invalid"
    assert records[1].degradation is not None
    assert records[1].degradation.member_id == "mem_missing"
    assert bridge.reads == [
        ("commitlog_1", None, 128),
        ("commitlog_1", 41, 128),
    ]
    assert bridge.releases == ["commitlog_1"]


def test_log_output_releases_when_reading_fails() -> None:
    bridge = LogBridge()

    async def fail_read(*args: Any, **kwargs: Any) -> LogOutputRead:
        raise GwzBridgeError("decode failed")

    bridge.log_output_read = fail_read  # type: ignore[method-assign]
    client = Client(bridge=bridge)  # type: ignore[arg-type]

    async def collect() -> None:
        async for _ in client.log_output(LogOutputLogRef(log_id="commitlog_bad")):
            pass

    with pytest.raises(GwzBridgeError, match="decode failed"):
        asyncio.run(collect())
    assert bridge.releases == ["commitlog_bad"]


def test_log_output_releases_when_consumer_closes_early() -> None:
    bridge = LogBridge()
    client = Client(bridge=bridge)  # type: ignore[arg-type]

    async def consume_one() -> LogOutputRecord:
        output = client.log_output("commitlog_cancelled")
        first = await anext(output)
        await output.aclose()
        return first

    assert asyncio.run(consume_one()).kind is LogOutputRecordKind.entry
    assert bridge.releases == ["commitlog_cancelled"]


class _PagedNativeLog:
    def __init__(self, pages: list[tuple[list[LogOutputRecord], int, str]]) -> None:
        self.pages = list(pages)
        self.reads: list[tuple[str, int | None, int | None]] = []
        self.releases: list[str] = []

    def log_output_read(
        self,
        log_id: str,
        cursor: int | None,
        max_records: int | None,
    ) -> tuple[list[bytes], int, str]:
        self.reads.append((log_id, cursor, max_records))
        records, next_cursor, state = self.pages.pop(0)
        return (
            [encode_message("LogOutputRecord", record) for record in records],
            next_cursor,
            state,
        )

    def log_output_release(self, log_id: str) -> None:
        self.releases.append(log_id)


def test_native_bridge_preserves_rich_multi_page_record_order_and_exact_fields() -> None:
    expected = [
        _rich_degradation_record("@root", LogDegradationReason.unborn),
        _rich_entry_record(1),
        _rich_entry_record(2, marker_invalid=True),
        _rich_degradation_record(
            "mem_missing", LogDegradationReason.repository_unreadable
        ),
    ]
    native = _PagedNativeLog(
        [
            (expected[:2], 19, "data"),
            (expected[2:], 41, "data"),
            ([], 41, "eof"),
        ]
    )
    bridge = NativeCoreBridge(native=native)  # type: ignore[arg-type]
    client = Client(bridge=bridge)

    async def collect() -> list[LogOutputRecord]:
        return [record async for record in client.log_output("commitlog_rich")]

    records = asyncio.run(collect())

    assert records == expected
    assert native.reads == [
        ("commitlog_rich", None, 128),
        ("commitlog_rich", 19, 128),
        ("commitlog_rich", 41, 128),
    ]
    assert native.releases == ["commitlog_rich"]


def test_log_output_releases_exactly_once_when_blocked_read_is_cancelled() -> None:
    bridge = LogBridge()
    client = Client(bridge=bridge)  # type: ignore[arg-type]

    async def scenario() -> None:
        started = asyncio.Event()
        blocked = asyncio.Event()

        async def blocked_read(*args: Any, **kwargs: Any) -> LogOutputRead:
            started.set()
            await blocked.wait()
            raise AssertionError("cancelled read unexpectedly resumed")

        bridge.log_output_read = blocked_read  # type: ignore[method-assign]
        output = client.log_output("commitlog_blocked")
        task = asyncio.create_task(anext(output))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert bridge.releases == ["commitlog_blocked"]

    asyncio.run(scenario())
