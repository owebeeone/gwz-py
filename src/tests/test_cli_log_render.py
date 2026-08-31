"""S3.6 Python rendering and exact Rust-output parity acceptance."""

from __future__ import annotations

import asyncio
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any

import pytest

import gwz.cli as cli_module
import gwz.cli_log as cli_log_module
from gwz.cli import build_parser
from gwz.cli_log import handle_log
from gwz.cli_render import (
    log_color_enabled,
    render_log_degradation,
    render_log_entry,
    render_log_record_json,
)
from gwz.cli_shared import CommandContext, meta_kwargs
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
    LogOutputLogRef,
    LogOutputRecord,
    LogOutputRecordKind,
    LogResponse,
    ResponseEnvelope,
    ResponseMeta,
    SourceKind,
)


MARKER = "01987b0c-2f75-7c4a-9a32-8fd22f7d7c91"
HASH_A = "a" * 40
HASH_B = "b" * 40
PARENT_1 = "1" * 40
PARENT_2 = "2" * 40


def _identity(name: str, email: str, offset: int) -> GitObjectIdentity:
    return GitObjectIdentity(
        name=name,
        email=email,
        time_ms=None,
        timezone_offset_minutes=offset,
    )


def _member(member_id: str, path: str, commit: str, parents: list[str]) -> LogEntryMember:
    return LogEntryMember(
        member_id=member_id,
        member_path=path,
        source_kind=SourceKind.git,
        commit=commit,
        parents=parents,
    )


def _entry_record(
    *,
    members: list[LogEntryMember] | None = None,
    provenance: LogMergeProvenance | None = None,
    subject: str = "subject\ncontrol\0",
    body: str | None = '\nbody "quoted"\\tail',
    lossy: bool | None = True,
) -> LogOutputRecord:
    return LogOutputRecord(
        kind=LogOutputRecordKind.entry,
        entry=LogEntry(
            members=members
            or [
                _member("mem_a", "members/a", HASH_A, [PARENT_2, PARENT_1]),
                _member("mem_z", "members/z", HASH_B, []),
            ],
            provenance=provenance
            or LogMergeProvenance(kind=LogMergeKind.marker, gwz_commit_id=MARKER),
            author=_identity("Author �", "author@example.test", 630),
            committer=_identity("Committer", "commit@example.test", -345),
            subject=subject,
            body=body,
            ordering_timestamp_ms=22_000,
            author_timestamp_seconds=-7,
            committer_timestamp_seconds=22,
            ordering_timestamp_seconds=22,
            lossy=lossy,
        ),
        degradation=None,
    )


def _degradation_record(
    reason: LogDegradationReason = LogDegradationReason.revision_unresolved,
) -> LogOutputRecord:
    return LogOutputRecord(
        kind=LogOutputRecordKind.degradation,
        entry=None,
        degradation=LogDegradation(
            member_id="mem_bad",
            member_path="members/bad",
            source_kind=SourceKind.git,
            reason=reason,
            operand="missing..HEAD",
            message="cannot\nresolve",
        ),
    )


def _contract_records() -> list[LogOutputRecord]:
    invalid = _entry_record(
        members=[_member("@root", ".", HASH_B, [PARENT_1])],
        provenance=LogMergeProvenance(
            kind=LogMergeKind.none,
            gwz_commit_id="marker-invalid",
        ),
        subject="literal �",
        body=None,
        lossy=False,
    )
    return [_entry_record(), _degradation_record(), invalid]


RUST_ENTRY_ORACLE = (
    '{"author":{"email":"author@example.test","name":"Author �",'
    '"time":{"offset_min":630,"time":-7}},'
    '"body":"\\nbody \\"quoted\\"\\\\tail",'
    '"committer":{"email":"commit@example.test","name":"Committer",'
    '"time":{"offset_min":-345,"time":22}},"lossy":true,'
    f'"members":[{{"hash":"{HASH_A}","member_id":"mem_a",'
    f'"member_path":"members/a","parents":["{PARENT_2}","{PARENT_1}"]}},'
    f'{{"hash":"{HASH_B}","member_id":"mem_z","member_path":"members/z",'
    '"parents":[]}],'
    f'"provenance":"marker:{MARKER}","record":"entry",'
    '"subject":"subject\\ncontrol\\u0000"}'
)
RUST_DEGRADATION_ORACLE = (
    '{"member_id":"mem_bad","member_path":"members/bad",'
    '"message":"cannot\\nresolve","operand":"missing..HEAD",'
    '"reason":"revision_unresolved","record":"degradation"}'
)
RUST_INVALID_ORACLE = (
    '{"author":{"email":"author@example.test","name":"Author �",'
    '"time":{"offset_min":630,"time":-7}},'
    '"committer":{"email":"commit@example.test","name":"Committer",'
    '"time":{"offset_min":-345,"time":22}},'
    f'"members":[{{"hash":"{HASH_B}","member_id":"@root",'
    f'"member_path":".","parents":["{PARENT_1}"]}}],'
    '"provenance":"marker-invalid","record":"entry","subject":"literal �"}'
)
RUST_JSON_ORACLE = (
    '{"records":['
    + ",".join([RUST_ENTRY_ORACLE, RUST_DEGRADATION_ORACLE, RUST_INVALID_ORACLE])
    + '],"schema":"gwz.log/v0"}\n'
)
RUST_JSONL_ORACLE = (
    '{"record":"header","schema":"gwz.log/v0"}\n'
    + "\n".join([RUST_ENTRY_ORACLE, RUST_DEGRADATION_ORACLE, RUST_INVALID_ORACLE])
    + "\n"
)


def test_human_rendering_matches_captured_rust_compact_and_full_bytes() -> None:
    entry = _entry_record().entry
    assert entry is not None
    entry.author = _identity("Ada Lovelace", "ada@example.test", -60)
    entry.committer = _identity("Grace Hopper", "grace@example.test", 330)
    entry.author_timestamp_seconds = 0
    entry.committer_timestamp_seconds = 0
    entry.subject = "full subject"
    entry.body = "\nbody line\nsecond line"

    assert render_log_entry(entry, full=False, color=False) == (
        "1970-01-01 05:30:00 +0530 [members/a, members/z] "
        "aaaaaaaaaaaa full subject"
    )
    assert render_log_entry(entry, full=True, color=False) == (
        f"commit {HASH_A}\n"
        "Members:\n"
        "    ID     PATH       COMMIT\n"
        f"    mem_a  members/a  {HASH_A}\n"
        f"    mem_z  members/z  {HASH_B}\n"
        "Author: Ada Lovelace <ada@example.test>\n"
        "Date:   1969-12-31 23:00:00 -0100\n\n"
        "    full subject\n"
        "    \n"
        "    body line\n"
        "    second line"
    )


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-(1 << 63), "-292277022657-01-27 13:59:52 +0530"),
        ((1 << 63) - 1, "292277026596-12-04 21:00:07 +0530"),
    ],
)
def test_human_dates_match_rust_for_entire_i64_domain(
    seconds: int, expected: str
) -> None:
    entry = _entry_record().entry
    assert entry is not None
    entry.committer.timezone_offset_minutes = 330
    entry.committer_timestamp_seconds = seconds
    assert render_log_entry(entry, full=False, color=False).startswith(expected)


def test_human_sanitization_member_boundaries_color_and_degradation_match_rust() -> None:
    entry = _entry_record(
        members=[_member("mem_bad", "members/�\x1b", HASH_A, [])],
        subject="fix\tthing\x1b[31m",
        body="body\tcell\nnext\x07line",
    ).entry
    assert entry is not None
    entry.author.name = "Ad\0a�"
    compact = render_log_entry(entry, full=False, color=False)
    full = render_log_entry(entry, full=True, color=False)
    assert "members/��" in compact
    assert "fix thing�[31m" in compact
    assert "Author: Ad�a� <author@example.test>" in full
    assert "    body cell\n    next�line" in full
    assert "\x1b" not in compact + full
    assert "\t" not in compact + full

    degradation = _degradation_record().degradation
    assert degradation is not None
    degradation.member_path = "members/api\x1b"
    degradation.operand = "topic\tname"
    degradation.message = "missing\x07 ref"
    assert render_log_degradation(degradation, color=False) == (
        "gwz log: degraded members/api�: revision unresolved for "
        "'topic name' — missing� ref"
    )
    assert log_color_enabled("auto", False) is False
    assert log_color_enabled("auto", True) is True
    assert "\x1b[" in render_log_entry(entry, full=False, color=True)


def test_compact_member_set_boundaries_match_rust() -> None:
    small = _entry_record(
        members=[
            _member("@root", ".", HASH_A, []),
            _member("mem_api", "members/api", HASH_B, []),
            _member("mem_web", "members/web", HASH_A, []),
        ]
    ).entry
    root_large = _entry_record(
        members=[
            _member("@root", ".", HASH_A, []),
            _member("mem_a", "a", HASH_A, []),
            _member("mem_b", "b", HASH_A, []),
            _member("mem_c", "c", HASH_A, []),
        ]
    ).entry
    member_large = _entry_record(
        members=[
            _member(f"mem_{letter}", letter, HASH_A, [])
            for letter in ["a", "b", "c", "d"]
        ]
    ).entry
    assert small is not None and root_large is not None and member_large is not None
    assert "[., members/api, members/web]" in render_log_entry(
        small, full=False, color=False
    )
    assert "[root+3]" in render_log_entry(root_large, full=False, color=False)
    assert "[4 members]" in render_log_entry(member_large, full=False, color=False)


@pytest.mark.parametrize(
    ("reason", "human", "machine"),
    [
        (
            LogDegradationReason.repository_unreadable,
            "repository unreadable",
            "repository_unreadable",
        ),
        (LogDegradationReason.repository_missing, "repository missing", "repository_missing"),
        (LogDegradationReason.unborn, "unborn history", "unborn"),
        (LogDegradationReason.revision_unresolved, "revision unresolved", "revision_unresolved"),
        (
            LogDegradationReason.snapshot_entry_missing,
            "snapshot entry missing",
            "snapshot_entry_missing",
        ),
        (LogDegradationReason.lock_entry_missing, "lock entry missing", "lock_entry_missing"),
        (
            LogDegradationReason.unsupported_source_kind,
            "unsupported source kind",
            "unsupported_source_kind",
        ),
    ],
)
def test_all_degradation_labels_and_tokens_match_rust(
    reason: LogDegradationReason, human: str, machine: str
) -> None:
    record = _degradation_record(reason)
    assert record.degradation is not None
    assert human in render_log_degradation(record.degradation, color=False)
    assert f'"reason":"{machine}"' in render_log_record_json(record)


def test_degradation_optional_context_and_empty_path_match_rust() -> None:
    record = _degradation_record()
    assert record.degradation is not None
    record.degradation.member_path = ""
    record.degradation.operand = None
    record.degradation.message = None
    assert render_log_degradation(record.degradation, color=False) == (
        "gwz log: degraded mem_bad: revision unresolved"
    )
    assert render_log_record_json(record) == (
        '{"member_id":"mem_bad","member_path":"","message":null,'
        '"operand":null,"reason":"revision_unresolved",'
        '"record":"degradation"}'
    )


def test_machine_record_bytes_match_captured_rust_oracles_at_lossy_edge() -> None:
    rendered = [render_log_record_json(record) for record in _contract_records()]
    assert rendered == [
        RUST_ENTRY_ORACLE,
        RUST_DEGRADATION_ORACLE,
        RUST_INVALID_ORACLE,
    ]
    assert "�" in rendered[0] and '"lossy":true' in rendered[0]
    assert "�" in rendered[2] and '"lossy"' not in rendered[2]


def _response(status: AggregateStatus = AggregateStatus.ok) -> LogResponse:
    return LogResponse(
        response=ResponseEnvelope(
            meta=ResponseMeta(
                request_id="req_s36",
                schema_version="gwz.protocol/v0",
                action=ActionKind.log,
                aggregate_status=status,
                operation_id="op_s36",
                message=None,
                attribution=None,
            ),
            members=[],
            errors=[],
        ),
        output=LogOutputLogRef(log_id="commitlog_s36"),
    )


class _RenderClient:
    def __init__(
        self,
        records: list[LogOutputRecord],
        status: AggregateStatus = AggregateStatus.ok,
    ) -> None:
        self.records = records
        self.response = _response(status)
        self.reads = 0
        self.releases = 0
        self.output_stream: Any | None = None

    async def __aenter__(self) -> _RenderClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def log(self, _operands: list[str], **_kwargs: Any) -> LogResponse:
        return self.response

    def log_output(self, _log_ref: LogOutputLogRef):
        async def stream():
            try:
                for record in self.records:
                    self.reads += 1
                    yield record
            finally:
                self.releases += 1

        self.output_stream = stream()
        return self.output_stream

    async def _release_log_output(self, _log_ref: LogOutputLogRef) -> None:
        self.releases += 1


def _install_client(monkeypatch: pytest.MonkeyPatch, client: _RenderClient) -> None:
    monkeypatch.setattr(cli_module, "Client", lambda root=None: client)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--json", "log", "--body"], RUST_JSON_ORACLE),
        (["--jsonl", "log", "--body"], RUST_JSONL_ORACLE),
    ],
)
def test_actual_python_cli_machine_bytes_match_rust_and_release(
    argv: list[str],
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _RenderClient(_contract_records(), AggregateStatus.partial)
    _install_client(monkeypatch, client)
    assert cli_module.main(argv) == 1
    assert capsys.readouterr() == (expected, "")
    assert client.reads == 3
    assert client.releases == 1


class _ByteStream:
    def __init__(self) -> None:
        self.buffer = BytesIO()

    def write(self, _value: str) -> int:
        raise AssertionError("machine parity must use the UTF-8 byte stream")

    def isatty(self) -> bool:
        return False


def test_actual_machine_output_uses_exact_utf8_bytes_without_text_translation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RenderClient(_contract_records())
    writer = _ByteStream()
    _install_client(monkeypatch, client)
    monkeypatch.setattr(cli_log_module.sys, "stdout", writer)
    assert cli_module.main(["--jsonl", "log", "--body"]) == 0
    assert writer.buffer.getvalue() == RUST_JSONL_ORACLE.encode("utf-8")
    assert client.releases == 1


class _TextStream(StringIO):
    def __init__(self, tty: bool) -> None:
        super().__init__()
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


@pytest.mark.parametrize(
    ("color", "tty", "enabled"),
    [
        ("always", False, True),
        ("never", True, False),
        ("auto", False, False),
        ("auto", True, True),
    ],
)
def test_actual_human_color_matches_rust_stdout_tty_policy(
    color: str,
    tty: bool,
    enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RenderClient([_entry_record()])
    writer = _TextStream(tty)
    _install_client(monkeypatch, client)
    monkeypatch.setattr(cli_log_module.sys, "stdout", writer)
    assert cli_module.main(["log", "--color", color]) == 0
    assert ("\x1b[" in writer.getvalue()) is enabled
    assert client.releases == 1


def test_actual_python_cli_human_channels_full_flag_and_release(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = _RenderClient([_entry_record(), _degradation_record()])
    _install_client(monkeypatch, client)
    assert cli_module.main(["log", "--full", "--body", "--color", "never"]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith(f"commit {HASH_A}\nMembers:\n")
    assert "    body \"quoted\"\\tail\n\n" in captured.out
    assert captured.err == (
        "gwz log: degraded members/bad: revision unresolved for "
        "'missing..HEAD' — cannot�resolve\n"
    )
    assert client.releases == 1


class _BrokenWriter:
    def __init__(self, error: OSError) -> None:
        self.error = error
        self.writes = 0
        self.flushes = 0

    def write(self, _value: str) -> int:
        self.writes += 1
        raise self.error

    def flush(self) -> None:
        self.flushes += 1

    def isatty(self) -> bool:
        return False


class _BreakAfterWriter(_BrokenWriter):
    def __init__(
        self,
        successful_writes: int,
        error: OSError | None = None,
    ) -> None:
        super().__init__(error or BrokenPipeError("closed"))
        self.successful_writes = successful_writes

    def write(self, value: str) -> int:
        self.writes += 1
        if self.writes > self.successful_writes:
            raise self.error
        return len(value)


class _FailOnceWriter(_BrokenWriter):
    def __init__(self, error: OSError) -> None:
        super().__init__(error)
        self.values: list[str] = []

    def write(self, value: str) -> int:
        self.writes += 1
        if self.writes == 1:
            raise self.error
        self.values.append(value)
        return len(value)


class _FlushBrokenWriter(_BrokenWriter):
    def write(self, value: str) -> int:
        self.writes += 1
        return len(value)

    def flush(self) -> None:
        self.flushes += 1
        raise self.error


def test_machine_prefix_epipe_stops_before_read_releases_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RenderClient(_contract_records())
    writer = _BrokenWriter(BrokenPipeError("closed"))
    _install_client(monkeypatch, client)
    monkeypatch.setattr(cli_log_module.sys, "stdout", writer)
    assert cli_module.main(["--jsonl", "log"]) == 0
    assert writer.writes == 1
    assert client.reads == 0
    assert client.releases == 1


def test_machine_prefix_flush_epipe_stops_before_read_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RenderClient(_contract_records())
    writer = _FlushBrokenWriter(BrokenPipeError("closed"))
    _install_client(monkeypatch, client)
    monkeypatch.setattr(cli_log_module.sys, "stdout", writer)
    assert cli_module.main(["--json", "log"]) == 0
    assert writer.flushes == 1
    assert client.reads == 0
    assert client.releases == 1


def test_machine_record_epipe_stops_before_later_records_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RenderClient(_contract_records())
    writer = _BreakAfterWriter(successful_writes=1)
    _install_client(monkeypatch, client)
    monkeypatch.setattr(cli_log_module.sys, "stdout", writer)
    assert cli_module.main(["--jsonl", "log"]) == 0
    assert writer.writes == 2
    assert client.reads == 1
    assert client.releases == 1


def test_machine_json_suffix_epipe_is_clean_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RenderClient([])
    writer = _BreakAfterWriter(successful_writes=1)
    _install_client(monkeypatch, client)
    monkeypatch.setattr(cli_log_module.sys, "stdout", writer)
    assert cli_module.main(["--json", "log"]) == 0
    assert writer.writes == 2
    assert client.reads == 0
    assert client.releases == 1


def test_machine_prefix_non_epipe_is_execution_failure_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RenderClient(_contract_records())
    writer = _BrokenWriter(OSError("disk full"))
    _install_client(monkeypatch, client)
    monkeypatch.setattr(cli_log_module.sys, "stdout", writer)
    assert cli_module.main(["--jsonl", "log"]) == 1
    assert client.reads == 0
    assert client.releases == 1


@pytest.mark.parametrize(
    ("argv", "records", "successful_writes", "expected_reads"),
    [
        (["log"], [_entry_record()], 0, 1),
        (["--jsonl", "log"], [_entry_record()], 1, 1),
        (["--json", "log"], [], 1, 0),
    ],
)
def test_ordinary_stdout_oserror_at_each_render_phase_is_one_and_releases(
    argv: list[str],
    records: list[LogOutputRecord],
    successful_writes: int,
    expected_reads: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RenderClient(records)
    writer = _BreakAfterWriter(
        successful_writes=successful_writes,
        error=OSError("disk full"),
    )
    _install_client(monkeypatch, client)
    monkeypatch.setattr(cli_log_module.sys, "stdout", writer)
    assert cli_module.main(argv) == 1
    assert client.reads == expected_reads
    assert client.releases == 1


def test_ordinary_degradation_stderr_oserror_is_typed_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RenderClient([_degradation_record()])
    writer = _FailOnceWriter(OSError("stderr disk full"))
    _install_client(monkeypatch, client)
    monkeypatch.setattr(cli_log_module.sys, "stderr", writer)
    assert cli_module.main(["log"]) == 1
    assert client.reads == 1
    assert client.releases == 1
    assert "".join(writer.values) == (
        "gwz: cannot write log stderr: stderr disk full\n"
    )


def test_human_epipe_stops_before_later_record_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RenderClient([_entry_record(), _entry_record(), _degradation_record()])
    writer = _BrokenWriter(BrokenPipeError("closed"))
    _install_client(monkeypatch, client)
    monkeypatch.setattr(cli_log_module.sys, "stdout", writer)
    assert cli_module.main(["log"]) == 0
    assert writer.writes == 1
    assert client.reads == 1
    assert client.releases == 1


def test_handle_releases_epipe_stream_before_returning_to_live_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        client = _RenderClient([_entry_record(), _entry_record()])
        writer = _BrokenWriter(BrokenPipeError("closed"))
        monkeypatch.setattr(cli_log_module.sys, "stdout", writer)
        args = build_parser().parse_args(["log"])
        context = CommandContext(
            args=args,
            client=client,  # type: ignore[arg-type]
            meta=meta_kwargs(args),
        )
        assert await handle_log(context) == cli_log_module.LogCliResult(exit_code=0)
        assert client.reads == 1
        assert client.releases == 1

    asyncio.run(scenario())


def test_stderr_epipe_is_execution_failure_not_clean_stdout_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _RenderClient([_degradation_record()])
    writer = _BrokenWriter(BrokenPipeError("closed"))
    _install_client(monkeypatch, client)
    monkeypatch.setattr(cli_log_module.sys, "stderr", writer)
    assert cli_module.main(["log"]) == 1
    assert writer.writes == 2
    assert client.reads == 1
    assert client.releases == 1


def test_invalid_record_is_typed_failure_and_releases(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    invalid = LogOutputRecord(
        kind=LogOutputRecordKind.entry,
        entry=None,
        degradation=None,
    )
    client = _RenderClient([invalid])
    _install_client(monkeypatch, client)
    assert cli_module.main(["log"]) == 1
    assert "does not match its payload" in capsys.readouterr().err
    assert client.releases == 1


@pytest.mark.parametrize("invalidity", ["offset", "provenance"])
def test_invalid_machine_entry_contract_is_typed_and_releases(
    invalidity: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    record = _entry_record()
    assert record.entry is not None
    if invalidity == "offset":
        record.entry.author.timezone_offset_minutes = None
    else:
        record.entry.provenance = LogMergeProvenance(
            kind=LogMergeKind.none,
            gwz_commit_id="unexpected-marker",
        )
    client = _RenderClient([record])
    _install_client(monkeypatch, client)
    assert cli_module.main(["--jsonl", "log"]) == 1
    captured = capsys.readouterr()
    assert '"code": "InternalError"' in captured.out
    assert "commit-log" in captured.out
    assert client.releases == 1


class _BlockingClient(_RenderClient):
    def __init__(self) -> None:
        super().__init__([])
        self.started = asyncio.Event()
        self.blocked = asyncio.Event()

    async def log_output(self, _log_ref: LogOutputLogRef):
        try:
            self.reads += 1
            self.started.set()
            await self.blocked.wait()
            raise AssertionError("cancelled render read unexpectedly resumed")
            yield  # pragma: no cover
        finally:
            self.releases += 1


def test_render_cancellation_releases_blocked_record_stream() -> None:
    async def scenario() -> None:
        client = _BlockingClient()
        args = build_parser().parse_args(["log"])
        context = CommandContext(
            args=args,
            client=client,  # type: ignore[arg-type]
            meta=meta_kwargs(args),
        )
        task = asyncio.create_task(handle_log(context))
        await client.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.releases == 1

    asyncio.run(scenario())


def test_log_help_and_readme_document_python_rendering_parity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["log", "--help"])
    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    normalized = " ".join(help_text.split())
    for phrase in [
        "--full",
        "git-style blocks",
        "workspace-relative member paths",
        "does not use a pager",
        "machine output",
    ]:
        assert phrase in normalized
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    assert "gwz-py log --full --body" in readme
    assert '"schema": "gwz.log/v0"' in readme


def test_machine_empty_output_oracles_are_exact(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for flag, expected in [
        ("--json", '{"records":[],"schema":"gwz.log/v0"}\n'),
        ("--jsonl", '{"record":"header","schema":"gwz.log/v0"}\n'),
    ]:
        client = _RenderClient([])
        _install_client(monkeypatch, client)
        assert cli_module.main([flag, "log"]) == 0
        assert capsys.readouterr() == (expected, "")
        assert client.releases == 1


def test_all_machine_provenance_tokens_are_exact() -> None:
    cases = [
        (LogMergeKind.none, None, "none"),
        (LogMergeKind.heuristic, None, "heuristic"),
        (LogMergeKind.marker, MARKER, f"marker:{MARKER}"),
        (LogMergeKind.none, "marker-invalid", "marker-invalid"),
    ]
    for kind, marker, expected in cases:
        record = _entry_record(
            provenance=LogMergeProvenance(kind=kind, gwz_commit_id=marker)
        )
        assert f'"provenance":"{expected}"' in render_log_record_json(record)
