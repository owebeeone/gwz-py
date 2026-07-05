from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

import gwz.cli as cli_module
from gwz.cli import build_parser
from gwz.cli_shared import CliUsageError, CommandContext, meta_kwargs, validate_args
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    DiffChunkEncoding,
    DiffFileEntry,
    DiffManifestMode,
    DiffManifestResponse,
    DiffOutputFormat,
    DiffOutputLogRef,
    DiffOutputRecord,
    DiffOutputRecordKind,
    DiffRepoScope,
    DiffStatus,
    DiffSummary,
    DiffWhitespaceMode,
    ResponseEnvelope,
    ResponseMeta,
    SourceKind,
)


def _response() -> ResponseEnvelope:
    return ResponseEnvelope(
        meta=ResponseMeta(
            request_id="req_cli_diff",
            schema_version="gwz.protocol/v0",
            action=ActionKind.diff,
            aggregate_status=AggregateStatus.ok,
            operation_id=None,
            message=None,
            attribution=None,
        ),
        members=[],
        errors=[],
    )


def _scope() -> DiffRepoScope:
    return DiffRepoScope(
        root=None,
        member_id="mem_app",
        member_path="repos/app",
        source_kind=SourceKind.git,
    )


def _entry(
    status: DiffStatus = DiffStatus.modified,
    *,
    old_path: str | None = None,
    new_path: str | None = "repos/app/file.txt",
    similarity: int | None = None,
    insertions: int | None = 2,
    deletions: int | None = 1,
    is_binary: bool | None = False,
) -> DiffFileEntry:
    return DiffFileEntry(
        file_id="mem_app#0",
        scope=_scope(),
        status=status,
        old_path=old_path,
        new_path=new_path,
        old_mode=0o100644,
        new_mode=0o100644,
        similarity=similarity,
        insertions=insertions,
        deletions=deletions,
        is_binary=is_binary,
    )


def _summary(has_differences: bool = True) -> DiffSummary:
    return DiffSummary(
        has_differences=has_differences,
        repos_examined=1,
        repos_with_differences=1 if has_differences else 0,
        files_changed=1 if has_differences else 0,
        insertions=2 if has_differences else 0,
        deletions=1 if has_differences else 0,
        repo_summaries=[],
    )


def _manifest(
    *,
    files: list[DiffFileEntry] | None = None,
    summary: DiffSummary | None = None,
    output: DiffOutputLogRef | None = None,
) -> DiffManifestResponse:
    return DiffManifestResponse(
        response=_response(),
        files=files or [],
        summary=summary,
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


class FakeDiffClient:
    def __init__(
        self,
        response: DiffManifestResponse | None = None,
        records: list[DiffOutputRecord] | None = None,
    ) -> None:
        self.root = None
        self.response = response or _manifest(summary=_summary(False))
        self.records = records or []
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def __aenter__(self) -> "FakeDiffClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def diff(self, *args: Any, **kwargs: Any) -> DiffManifestResponse:
        self.calls.append((args, kwargs))
        return self.response

    async def diff_output(self, log_ref: DiffOutputLogRef) -> Any:
        for record in self.records:
            yield record


def run_handler(argv: list[str], client: FakeDiffClient) -> Any:
    args = build_parser().parse_args(argv)
    validate_args(args)
    context = CommandContext(args=args, client=client, meta=meta_kwargs(args))
    return asyncio.run(args.command_handler(context))


def test_diff_handler_lowers_supported_flags_and_pathspecs(capfd: pytest.CaptureFixture[str]) -> None:
    client = FakeDiffClient()

    result = run_handler(
        [
            "diff",
            "--cached",
            "--merge-base",
            "-M90%",
            "--raw",
            "-z",
            "-U",
            "5",
            "--inter-hunk-context",
            "2",
            "--binary",
            "--text",
            "-w",
            "--src-prefix",
            "x/",
            "--dst-prefix",
            "y/",
            "--line-prefix",
            "> ",
            "HEAD",
            "--",
            "src",
            "+literal",
        ],
        client,
    )

    assert result.exit_code == 0
    assert capfd.readouterr().out == ""
    call_args, kwargs = client.calls[0]
    assert call_args == (["HEAD"],)
    assert kwargs["pathspecs"] == ["src", "+literal"]
    assert kwargs["cached"] is True
    assert kwargs["merge_base"] is True
    assert kwargs["output_format"] is DiffOutputFormat.raw
    assert kwargs["null_terminated"] is True
    assert kwargs["context_lines"] == 5
    assert kwargs["interhunk_lines"] == 2
    assert kwargs["binary"] is True
    assert kwargs["text"] is True
    assert kwargs["whitespace"] is DiffWhitespaceMode.ignore_all
    assert kwargs["find_renames"] is True
    assert kwargs["rename_threshold"] == 90
    assert kwargs["src_prefix"] == "x/"
    assert kwargs["dst_prefix"] == "y/"
    assert kwargs["line_prefix"] == "> "


def test_diff_pathspec_split_ignores_global_values_named_diff() -> None:
    args = build_parser().parse_args(["--root", "diff", "diff", "HEAD", "--", "src"])

    assert args.root == "diff"
    assert args.operands == ["HEAD"]
    assert args.pathspecs == ["src"]


def test_diff_rejects_mutually_exclusive_output_formats() -> None:
    with pytest.raises(CliUsageError, match="mutually exclusive"):
        run_handler(["diff", "--stat", "--name-only"], FakeDiffClient())


def test_diff_no_renames_lowers_and_conflicts_with_find_renames() -> None:
    client = FakeDiffClient()

    result = run_handler(["diff", "--no-renames"], client)

    assert result.exit_code == 0
    assert client.calls[0][1]["find_renames"] is False
    with pytest.raises(CliUsageError, match="mutually exclusive"):
        run_handler(["diff", "--no-renames", "-M"], FakeDiffClient())


def test_diff_quiet_uses_any_difference_and_exit_code(
    capfd: pytest.CaptureFixture[str],
) -> None:
    client = FakeDiffClient(_manifest(summary=_summary(True)))

    result = run_handler(["diff", "--quiet"], client)

    assert result.exit_code == 1
    assert capfd.readouterr().out == ""
    kwargs = client.calls[0][1]
    assert kwargs["output_format"] is DiffOutputFormat.no_patch
    assert kwargs["manifest_mode"] is DiffManifestMode.any_difference


def test_cli_run_streams_patch_bytes_and_uses_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    output = DiffOutputLogRef(
        log_id="log-1",
        format=DiffOutputFormat.patch,
        encoding=DiffChunkEncoding.bytes,
    )
    client = FakeDiffClient(
        _manifest(summary=_summary(True), output=output),
        [_record(DiffOutputRecordKind.patch_bytes, b"diff --git a/f b/f\n")],
    )

    def client_factory(root: str | None = None) -> FakeDiffClient:
        client.root = root
        return client

    monkeypatch.setattr(cli_module, "Client", client_factory)
    args = build_parser().parse_args(["--root", "/ws", "diff", "--exit-code"])

    assert asyncio.run(cli_module.run(args)) == 1
    captured = capfd.readouterr()
    assert captured.out == "diff --git a/f b/f\n"
    assert captured.err == ""
    assert client.root == "/ws"


def test_diff_manifest_name_status_z_output(capfd: pytest.CaptureFixture[str]) -> None:
    client = FakeDiffClient(
        _manifest(
            files=[
                _entry(
                    DiffStatus.renamed,
                    old_path="repos/app/old.txt",
                    new_path="repos/app/new.txt",
                    similarity=87,
                ),
                _entry(DiffStatus.modified, new_path="repos/app/file.txt"),
            ],
            summary=_summary(True),
        )
    )

    result = run_handler(["diff", "--name-status", "-z"], client)

    assert result.exit_code == 0
    assert capfd.readouterr().out == (
        "R087\0repos/app/old.txt\0repos/app/new.txt\0"
        "M\0repos/app/file.txt\0"
    )


def test_diff_jsonl_outputs_manifest_and_base64_records(
    capfd: pytest.CaptureFixture[str],
) -> None:
    output = DiffOutputLogRef(
        log_id="log-jsonl",
        format=DiffOutputFormat.patch,
        encoding=DiffChunkEncoding.bytes,
    )
    client = FakeDiffClient(
        _manifest(files=[_entry()], summary=_summary(True), output=output),
        [_record(DiffOutputRecordKind.patch_bytes, b"abc")],
    )

    result = run_handler(["--jsonl", "diff"], client)

    assert result.exit_code == 0
    lines = [json.loads(line) for line in capfd.readouterr().out.splitlines()]
    assert [line["kind"] for line in lines] == [
        "diff_summary",
        "diff_file",
        "diff_output",
    ]
    assert lines[0]["has_differences"] is True
    assert lines[1]["entry"]["new_path"] == "repos/app/file.txt"
    assert lines[2]["record_kind"] == "patch_bytes"
    assert lines[2]["data_base64"] == "YWJj"


def test_native_cli_diff_name_only_smoke(
    tmp_path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    from native_helpers import create_workspace_with_member, native_module

    native_module()
    repo, _commit = create_workspace_with_member(tmp_path)
    (repo / "README.md").write_text("two\n", encoding="utf-8")
    args = build_parser().parse_args(["--root", str(tmp_path), "diff", "--name-only"])

    assert asyncio.run(cli_module.run(args)) == 0
    assert capfd.readouterr().out == "repos/app/README.md\n"
