"""S3.5 gwz-py log command surface, lowering, and process lifecycle."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import textwrap
from typing import Any

import pytest

import gwz.cli as cli_module
from gwz.cli import build_parser
from gwz.cli_log import (
    LogCliResult,
    exit_code_for_log_error,
    exit_code_for_log_response,
    handle_log,
)
from gwz.cli_shared import CommandContext, meta_kwargs
from gwz.errors import GwzBridgeError
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    LogDegradation,
    LogDegradationReason,
    LogOutputLogRef,
    LogOutputRecord,
    LogOutputRecordKind,
    LogResponse,
    ResponseEnvelope,
    ResponseMeta,
    SourceKind,
    SyncBehavior,
)


def _response(status: AggregateStatus) -> LogResponse:
    return LogResponse(
        response=ResponseEnvelope(
            meta=ResponseMeta(
                request_id="req_cli_log",
                schema_version="gwz.protocol/v0",
                action=ActionKind.log,
                aggregate_status=status,
                operation_id="op_cli_log",
                message=None,
                attribution=None,
            ),
            members=[],
            errors=[],
        ),
        output=LogOutputLogRef(log_id="commitlog_cli"),
    )


def _degradation() -> LogOutputRecord:
    return LogOutputRecord(
        kind=LogOutputRecordKind.degradation,
        entry=None,
        degradation=LogDegradation(
            member_id="mem_missing",
            member_path="repos/missing",
            source_kind=SourceKind.git,
            reason=LogDegradationReason.repository_unreadable,
            operand=None,
            message="unreadable",
        ),
    )


class FakeLogClient:
    def __init__(self, status: AggregateStatus = AggregateStatus.ok) -> None:
        self.response = _response(status)
        self.kwargs: dict[str, Any] | None = None
        self.operands: list[str] | None = None
        self.drained: list[LogOutputRecord] = []
        self.released = False

    async def log(self, operands: list[str], **kwargs: Any) -> LogResponse:
        self.operands = operands
        self.kwargs = kwargs
        return self.response

    async def log_output(self, log_ref: LogOutputLogRef):
        assert log_ref is self.response.output
        try:
            record = _degradation()
            self.drained.append(record)
            yield record
        finally:
            self.released = True


def test_log_parser_mirrors_full_s31_surface_and_splits_pathspecs() -> None:
    args = build_parser().parse_args(
        [
            "--target",
            "@root",
            "log",
            "-n",
            "0",
            "--since",
            "2026-08-31",
            "--until",
            "@200",
            "--author",
            "Dev <dev@invalid>",
            "--grep",
            "release",
            "--no-merges",
            "--first-parent",
            "--strict",
            "--no-coalesce",
            "--body",
            "--tagged",
            "--color",
            "never",
            "HEAD~2..HEAD",
            "+release.one",
            "--",
            ":(exclude)artifact",
            "+literal-path",
        ]
    )

    assert args.command == "log"
    assert args.max_entries == 0
    assert args.no_limit is False
    assert args.since == "2026-08-31"
    assert args.until == "@200"
    assert args.author == "Dev <dev@invalid>"
    assert args.grep == "release"
    assert args.no_merges is True
    assert args.first_parent is True
    assert args.strict is True
    assert args.no_coalesce is True
    assert args.body is True
    assert args.tagged is True
    assert args.color == "never"
    assert args.operands == ["HEAD~2..HEAD", "+release.one"]
    assert args.pathspecs == [":(exclude)artifact", "+literal-path"]
    assert meta_kwargs(args) == {"targets": ["@root"]}


def test_log_parser_preserves_absence_and_enforces_clap_conflicts() -> None:
    defaults = build_parser().parse_args(["log"])

    assert defaults.max_entries is None
    assert defaults.no_limit is False
    assert defaults.color == "auto"
    assert defaults.operands == []
    assert defaults.pathspecs == []
    for name in (
        "since",
        "until",
        "author",
        "grep",
    ):
        assert getattr(defaults, name) is None
    for name in (
        "no_merges",
        "first_parent",
        "strict",
        "no_coalesce",
        "body",
        "tagged",
    ):
        assert getattr(defaults, name) is False

    with pytest.raises(SystemExit):
        build_parser().parse_args(["log", "-n", "5", "--no-limit"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["log", "--color", "sometimes"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["log", "--since", "@1", "--since", "@2"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["log", "-n", "-1"])


def test_log_parser_rejects_caps_outside_signed_i64_without_dispatch(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert build_parser().parse_args(["log", "-n", str((1 << 63) - 1)]).max_entries == (
        1 << 63
    ) - 1

    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["log", "-n", str(1 << 63)])

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "signed 64-bit" in captured.err
    assert "Traceback" not in captured.err


def test_real_process_rejects_out_of_i64_cap_with_exit_two_and_no_traceback() -> None:
    environment = os.environ.copy()
    source = str(Path(__file__).resolve().parents[1])
    environment["PYTHONPATH"] = os.pathsep.join(
        [source, environment["PYTHONPATH"]]
        if environment.get("PYTHONPATH")
        else [source]
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from gwz.cli import main; "
                f"raise SystemExit(main(['log', '-n', '{1 << 63}']))"
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        timeout=30,
    )

    assert completed.returncode == 2
    assert completed.stdout == b""
    assert b"signed 64-bit" in completed.stderr
    assert b"Traceback" not in completed.stderr


@pytest.mark.parametrize(
    "option",
    [
        "--no-limit",
        "--no-merges",
        "--first-parent",
        "--strict",
        "--no-coalesce",
        "--body",
        "--tagged",
    ],
)
def test_log_parser_rejects_repeated_singleton_booleans(option: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["log", option, option])
    assert exc_info.value.code == 2


@pytest.mark.parametrize("option", ["--stric", "--no-lim", "--first-par"])
def test_log_parser_rejects_long_option_abbreviations(option: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["log", option])
    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["--targ", "@root", "log"],
        ["log", "--targ", "@root"],
    ],
)
def test_log_parser_rejects_global_abbreviations_before_and_after_command(
    argv: list[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(argv)
    assert exc_info.value.code == 2


def test_log_help_teaches_core_owned_grammars_and_strict_rule(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["log", "--help"])

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    normalized = " ".join(help_text.split())
    assert "Rust regex (not Git regex syntax)" in normalized
    assert "RFC3339/ISO-8601" in normalized
    assert "Promote any selected-repository degradation" in normalized
    assert "--no-limit" in help_text
    assert "--no-coalesce" in help_text


def test_log_handler_lowers_tri_states_drains_records_and_returns_aggregate_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    invocation = workspace / "repos" / "app"
    invocation.mkdir(parents=True)
    monkeypatch.chdir(invocation)
    args = build_parser().parse_args(
        [
            "--root",
            str(workspace),
            "--jobs",
            "3",
            "log",
            "--no-limit",
            "--no-coalesce",
            "--body",
            "HEAD",
            "--",
            "src",
        ]
    )
    client = FakeLogClient(AggregateStatus.partial)
    context = CommandContext(args=args, client=client, meta=meta_kwargs(args))  # type: ignore[arg-type]

    result = asyncio.run(handle_log(context))

    assert result == LogCliResult(exit_code=1)
    assert client.operands == ["HEAD"]
    assert client.kwargs == {
        "pathspecs": ["src"],
        "workspace_cwd": "repos/app",
        "max_entries": 0,
        "since": None,
        "until": None,
        "author": None,
        "grep": None,
        "no_merges": None,
        "first_parent": None,
        "strict": None,
        "coalesce": False,
        "include_body": True,
        "tagged": None,
        "concurrency": 3,
    }
    assert [record.kind for record in client.drained] == [
        LogOutputRecordKind.degradation
    ]
    assert client.released is True


def test_log_handler_lowers_every_active_cli_field_at_the_real_seam(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    invocation = workspace / "repos" / "app"
    invocation.mkdir(parents=True)
    monkeypatch.chdir(invocation)
    args = build_parser().parse_args(
        [
            "--root",
            str(workspace),
            "--target",
            "@root",
            "--jobs",
            "5",
            "--partial",
            "log",
            "--target",
            "mem_app",
            "--no-target",
            "mem_skip",
            "--member-path",
            "repos/app",
            "--all",
            "--dry-run",
            "--force",
            "--sync",
            "fetch-only",
            "--remote",
            "origin",
            "--max-per-host",
            "2",
            "--progress-interval",
            "11",
            "-n",
            "7",
            "--since",
            "2026-01-02",
            "--until",
            "@300",
            "--author",
            "Ada <ada@example.test>",
            "--grep",
            "release",
            "--no-merges",
            "--first-parent",
            "--strict",
            "--no-coalesce",
            "--body",
            "--tagged",
            "--color",
            "always",
            "main..topic",
            "+release.one",
            "--",
            ":(exclude)artifact",
            "src",
        ]
    )
    client = FakeLogClient(AggregateStatus.failed)
    context = CommandContext(args=args, client=client, meta=meta_kwargs(args))  # type: ignore[arg-type]

    result = asyncio.run(handle_log(context))

    assert result == LogCliResult(exit_code=1)
    assert client.response.response.meta.aggregate_status is AggregateStatus.failed
    assert client.operands == ["main..topic", "+release.one"]
    assert client.kwargs == {
        "pathspecs": [":(exclude)artifact", "src"],
        "workspace_cwd": "repos/app",
        "max_entries": 7,
        "since": "2026-01-02",
        "until": "@300",
        "author": "Ada <ada@example.test>",
        "grep": "release",
        "no_merges": True,
        "first_parent": True,
        "strict": True,
        "coalesce": False,
        "include_body": True,
        "tagged": True,
        "all_members": True,
        "targets": ["@root", "mem_app"],
        "exclude_targets": ["mem_skip"],
        "paths": ["repos/app"],
        "dry_run": True,
        "partial": True,
        "destructive": True,
        "sync": SyncBehavior.fetch_only,
        "remote": "origin",
        "concurrency": 5,
        "max_connections_per_host": 2,
        "progress_min_interval_ms": 11,
    }
    assert client.released is True


def test_log_handler_preserves_every_absent_cli_field() -> None:
    args = build_parser().parse_args(["log"])
    client = FakeLogClient()
    context = CommandContext(args=args, client=client, meta=meta_kwargs(args))  # type: ignore[arg-type]

    assert asyncio.run(handle_log(context)) == LogCliResult(exit_code=0)
    assert client.operands == []
    assert client.kwargs == {
        "pathspecs": [],
        "workspace_cwd": "",
        "max_entries": None,
        "since": None,
        "until": None,
        "author": None,
        "grep": None,
        "no_merges": None,
        "first_parent": None,
        "strict": None,
        "coalesce": None,
        "include_body": None,
        "tagged": None,
    }


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (None, 1),
        ("IoError", 1),
        ("InternalError", 1),
        ("GitCommandFailed", 1),
        ("ExternalToolMissing", 1),
        ("RemoteRejected", 1),
        ("SnapshotNotFound", 2),
        ("TagNotFound", 2),
        ("ManifestNotFound", 2),
        ("MemberInactive", 2),
    ],
)
def test_log_error_exit_classification_matches_s31(code: str | None, expected: int) -> None:
    assert exit_code_for_log_error(GwzBridgeError("failure", code=code)) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (AggregateStatus.ok, 0),
        (AggregateStatus.dirty, 0),
        (AggregateStatus.partial, 1),
        (AggregateStatus.failed, 1),
        (AggregateStatus.rejected, 2),
    ],
)
def test_log_response_exit_mapping_matches_s31(
    status: AggregateStatus,
    expected: int,
) -> None:
    assert exit_code_for_log_response(_response(status)) == expected


def test_cli_log_returns_partial_exit_without_rendering_records(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeLogClient(AggregateStatus.partial)

    class ContextClient:
        async def __aenter__(self) -> FakeLogClient:
            return client

        async def __aexit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(cli_module, "Client", lambda root=None: ContextClient())

    assert cli_module.main(["log"]) == 1
    assert client.released is True
    assert capsys.readouterr() == ("", "")


class _FailingLogClient(FakeLogClient):
    async def log(self, operands: list[str], **kwargs: Any) -> LogResponse:
        raise GwzBridgeError("missing snapshot", code="SnapshotNotFound")


class _ContextClient:
    def __init__(self, client: FakeLogClient) -> None:
        self.client = client

    async def __aenter__(self) -> FakeLogClient:
        return self.client

    async def __aexit__(self, *args: object) -> None:
        return None


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


@pytest.mark.parametrize("machine_flag", ["--json", "--jsonl"])
def test_log_machine_error_broken_pipe_is_immediate_success_without_spray(
    machine_flag: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = _BrokenWriter(BrokenPipeError("consumer closed"))
    monkeypatch.setattr(
        cli_module,
        "Client",
        lambda root=None: _ContextClient(_FailingLogClient()),
    )
    monkeypatch.setattr(cli_module.sys, "stdout", writer)

    assert cli_module.main([machine_flag, "log", "+missing"]) == 0
    assert writer.writes == 1
    assert writer.flushes == 0


def test_log_machine_error_non_epipe_output_failure_remains_exit_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = _BrokenWriter(OSError("write failed"))
    monkeypatch.setattr(
        cli_module,
        "Client",
        lambda root=None: _ContextClient(_FailingLogClient()),
    )
    monkeypatch.setattr(cli_module.sys, "stdout", writer)

    assert cli_module.main(["--json", "log", "+missing"]) == 1
    assert writer.writes == 1
    assert writer.flushes == 0


def test_real_log_machine_error_closed_pipe_exits_zero_without_stderr_spray() -> None:
    script = textwrap.dedent(
        """
        from gwz import cli
        from gwz.errors import GwzBridgeError

        async def fail(_args):
            raise GwzBridgeError("missing snapshot", code="SnapshotNotFound")

        cli.run = fail
        raise SystemExit(cli.main(["--json", "log", "+missing"]))
        """
    )
    environment = os.environ.copy()
    source = str(Path(__file__).resolve().parents[1])
    environment["PYTHONPATH"] = os.pathsep.join(
        [source, environment["PYTHONPATH"]]
        if environment.get("PYTHONPATH")
        else [source]
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    process.stdout.close()
    stderr = process.stderr.read()
    return_code = process.wait(timeout=30)

    assert return_code == 0
    assert stderr == b""
