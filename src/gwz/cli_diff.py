from __future__ import annotations

import argparse
import base64
import json
import sys
from dataclasses import dataclass
from typing import Any

from .cli_shared import CliUsageError, CommandContext, CommandRegistry, non_negative_int
from .protocol.generated import (
    DiffFileEntry,
    DiffManifestMode,
    DiffManifestResponse,
    DiffOutputFormat,
    DiffOutputRecord,
    DiffOutputRecordKind,
    DiffStatus,
    DiffWhitespaceMode,
)


@dataclass(frozen=True, slots=True)
class DiffCliResult:
    exit_code: int


def is_diff_result(value: object) -> bool:
    return isinstance(value, DiffCliResult)


def register_commands(registry: CommandRegistry) -> None:
    registry.register(
        "diff",
        help="Show workspace changes as one unified diff",
        configure=configure_diff,
        handler=handle_diff,
    )


def configure_diff(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cached",
        "--staged",
        dest="cached",
        action="store_true",
        help="Diff the index against HEAD",
    )
    parser.add_argument(
        "--merge-base",
        action="store_true",
        help="Use the merge base of the operand and HEAD as the old side",
    )
    parser.add_argument(
        "-M",
        "--find-renames",
        nargs="?",
        const="",
        default=None,
        metavar="n",
        help="Detect renames; optional similarity threshold",
    )
    parser.add_argument(
        "--no-renames",
        action="store_true",
        help="Disable rename detection",
    )

    parser.add_argument("--stat", action="store_true", help="Show a diffstat")
    parser.add_argument("--numstat", action="store_true", help="Show machine-readable diffstat")
    parser.add_argument("--shortstat", action="store_true", help="Show only the summary line")
    parser.add_argument("--summary", action="store_true", help="Show condensed summary")
    parser.add_argument("--name-only", action="store_true", help="Show only changed paths")
    parser.add_argument("--name-status", action="store_true", help="Show changed paths and status")
    parser.add_argument("--raw", action="store_true", help="Show raw diff format")

    parser.add_argument("-z", dest="null_terminated", action="store_true", help="Use NUL records")
    parser.add_argument(
        "-U",
        "--unified",
        type=non_negative_int,
        metavar="n",
        help="Generate diffs with n lines of context",
    )
    parser.add_argument(
        "--inter-hunk-context",
        dest="inter_hunk_context",
        type=non_negative_int,
        metavar="n",
        help="Show context between nearby hunks",
    )
    parser.add_argument("--binary", action="store_true", help="Emit binary patches")
    parser.add_argument("--text", action="store_true", help="Treat all files as text")
    parser.add_argument("-w", dest="ignore_all_space", action="store_true", help="Ignore all whitespace")
    parser.add_argument(
        "-b",
        dest="ignore_space_change",
        action="store_true",
        help="Ignore changes in amount of whitespace",
    )
    parser.add_argument(
        "--ignore-space-at-eol",
        action="store_true",
        help="Ignore whitespace at end of line",
    )
    parser.add_argument(
        "--ignore-blank-lines",
        action="store_true",
        help="Ignore changes whose lines are all blank",
    )
    parser.add_argument("--src-prefix", metavar="prefix", help="Use this source prefix")
    parser.add_argument("--dst-prefix", metavar="prefix", help="Use this destination prefix")
    parser.add_argument("--no-prefix", action="store_true", help="Do not show source/destination prefixes")
    parser.add_argument("--line-prefix", metavar="prefix", help="Prepend a prefix to every output line")

    parser.add_argument("--exit-code", action="store_true", help="Exit 1 if differences exist")
    parser.add_argument("--quiet", action="store_true", help="Suppress output and imply --exit-code")
    parser.add_argument("--no-pager", action="store_true", help="Accepted; output is written directly")

    parser.add_argument(
        "operands",
        nargs="*",
        help="Revisions, ranges, +snapshot ids, or pathspecs before --",
    )
    parser.set_defaults(pathspecs=[])


async def handle_diff(context: CommandContext) -> DiffCliResult:
    args = context.args
    requested_format = _output_format(args)
    display_format = requested_format or DiffOutputFormat.patch
    quiet = bool(args.quiet)
    exit_code = bool(args.exit_code or quiet)
    find_renames, rename_threshold = _rename_options(args)

    response = await context.client.diff(
        getattr(args, "operands", []) or [],
        pathspecs=getattr(args, "pathspecs", []) or [],
        cached=True if args.cached else None,
        merge_base=True if args.merge_base else None,
        output_format=DiffOutputFormat.no_patch if quiet else requested_format,
        manifest_mode=DiffManifestMode.any_difference if quiet else None,
        context_lines=args.unified,
        interhunk_lines=args.inter_hunk_context,
        whitespace=_whitespace_mode(args),
        find_renames=find_renames,
        rename_threshold=rename_threshold,
        binary=True if args.binary else None,
        text=True if args.text else None,
        null_terminated=True if args.null_terminated else None,
        src_prefix=args.src_prefix,
        dst_prefix=args.dst_prefix,
        no_prefix=True if args.no_prefix else None,
        line_prefix=args.line_prefix,
        **context.meta,
    )

    await _render_diff_output(context, response, display_format)
    has_differences = bool(
        response.summary is not None and response.summary.has_differences
    )
    return DiffCliResult(exit_code=1 if exit_code and has_differences else 0)


async def _render_diff_output(
    context: CommandContext,
    response: DiffManifestResponse,
    display_format: DiffOutputFormat,
) -> None:
    args = context.args
    if args.json:
        _write_stdout_text(manifest_json(response) + "\n")
        return
    if args.jsonl:
        _write_stdout_text(manifest_jsonl(response))
        if response.output is not None:
            async for record in context.client.diff_output(response.output):
                _write_stdout_text(output_record_json(record) + "\n")
        return
    if args.quiet:
        return
    if response.output is not None:
        await _stream_output_bytes(context, response)
        return
    _write_stdout_bytes(
        render_manifest_text(
            response,
            display_format,
            null_terminated=bool(args.null_terminated),
        )
    )


async def _stream_output_bytes(
    context: CommandContext,
    response: DiffManifestResponse,
) -> None:
    if response.output is None:
        return
    async for record in context.client.diff_output(response.output):
        if record.kind is DiffOutputRecordKind.patch_bytes:
            if record.data:
                _write_stdout_bytes(record.data)
        elif record.kind is DiffOutputRecordKind.stale_file:
            path = record.file_id or "<unknown>"
            diagnostic = record.diagnostic or path
            _write_stderr_text(
                f"gwz: warning: {diagnostic} changed during diff and was skipped (stale)\n"
            )
        elif record.kind is DiffOutputRecordKind.diagnostic and record.diagnostic:
            _write_stderr_text(f"gwz: {record.diagnostic}\n")


def _output_format(args: argparse.Namespace) -> DiffOutputFormat | None:
    selected: list[tuple[str, DiffOutputFormat]] = []
    formats = (
        ("--stat", "stat", DiffOutputFormat.stat),
        ("--numstat", "numstat", DiffOutputFormat.numstat),
        ("--shortstat", "shortstat", DiffOutputFormat.shortstat),
        ("--summary", "summary", DiffOutputFormat.summary),
        ("--name-only", "name_only", DiffOutputFormat.name_only),
        ("--name-status", "name_status", DiffOutputFormat.name_status),
        ("--raw", "raw", DiffOutputFormat.raw),
    )
    for flag, attr, output_format in formats:
        if getattr(args, attr, False):
            selected.append((flag, output_format))
    if len(selected) > 1:
        raise CliUsageError(f"{selected[0][0]} and {selected[1][0]} are mutually exclusive")
    return selected[0][1] if selected else None


def _rename_options(args: argparse.Namespace) -> tuple[bool | None, int | None]:
    spec = args.find_renames
    if args.no_renames and spec is not None:
        raise CliUsageError("--no-renames and --find-renames are mutually exclusive")
    if args.no_renames:
        return False, None
    if spec is None:
        return None, None
    threshold_text = spec.strip().rstrip("%")
    if not threshold_text:
        return True, None
    try:
        threshold = int(threshold_text)
    except ValueError as exc:
        raise CliUsageError(f"invalid rename threshold '{spec}'") from exc
    if not 0 <= threshold <= 100:
        raise CliUsageError(f"rename threshold must be between 0 and 100, got {threshold}")
    return True, threshold


def _whitespace_mode(args: argparse.Namespace) -> DiffWhitespaceMode | None:
    selected: list[tuple[str, DiffWhitespaceMode]] = []
    modes = (
        ("-w", "ignore_all_space", DiffWhitespaceMode.ignore_all),
        ("-b", "ignore_space_change", DiffWhitespaceMode.ignore_change),
        ("--ignore-space-at-eol", "ignore_space_at_eol", DiffWhitespaceMode.ignore_eol),
        ("--ignore-blank-lines", "ignore_blank_lines", DiffWhitespaceMode.ignore_blank_lines),
    )
    for flag, attr, mode in modes:
        if getattr(args, attr, False):
            selected.append((flag, mode))
    if len(selected) > 1:
        raise CliUsageError(f"{selected[0][0]} and {selected[1][0]} are mutually exclusive")
    return selected[0][1] if selected else None


def render_manifest_text(
    response: DiffManifestResponse,
    output_format: DiffOutputFormat,
    *,
    null_terminated: bool = False,
) -> bytes:
    separator = b"\0" if null_terminated else b"\n"
    if output_format is DiffOutputFormat.name_only:
        return _name_only(response.files, separator)
    if output_format is DiffOutputFormat.name_status:
        return _name_status(response.files, separator)
    if output_format is DiffOutputFormat.numstat:
        return _numstat(response.files, separator)
    if output_format is DiffOutputFormat.stat:
        return _stat(response.files)
    if output_format is DiffOutputFormat.shortstat:
        return _shortstat(response.files)
    if output_format is DiffOutputFormat.summary:
        return _summary(response.files)
    return b""


def manifest_json(response: DiffManifestResponse) -> str:
    return json.dumps(
        {
            "kind": "diff",
            "files": [_file_entry_json(entry) for entry in response.files],
            "summary": _summary_json(response.summary),
            "excluded_targets": [
                _excluded_target_json(target) for target in response.excluded_targets
            ],
        },
        sort_keys=True,
    )


def manifest_jsonl(response: DiffManifestResponse) -> str:
    lines: list[str] = []
    if response.summary is not None:
        lines.append(
            json.dumps(
                {
                    "kind": "diff_summary",
                    "has_differences": response.summary.has_differences,
                    "files_changed": response.summary.files_changed,
                    "insertions": response.summary.insertions,
                    "deletions": response.summary.deletions,
                },
                sort_keys=True,
            )
        )
    lines.extend(
        json.dumps(
            {"kind": "diff_file", "entry": _file_entry_json(entry)},
            sort_keys=True,
        )
        for entry in response.files
    )
    return "".join(f"{line}\n" for line in lines)


def output_record_json(record: DiffOutputRecord) -> str:
    data = record.data
    return json.dumps(
        {
            "kind": "diff_output",
            "record_kind": _enum_name(record.kind),
            "file_id": record.file_id,
            "data_base64": (
                base64.b64encode(data).decode("ascii") if data is not None else None
            ),
            "stale": record.stale,
            "diagnostic": record.diagnostic,
        },
        sort_keys=True,
    )


def _primary_path(entry: DiffFileEntry) -> str:
    return entry.new_path or entry.old_path or ""


def _status_letter(status: DiffStatus) -> str:
    return {
        "added": "A",
        "modified": "M",
        "deleted": "D",
        "renamed": "R",
        "copied": "C",
        "type_changed": "T",
        "unmerged": "U",
    }.get(_enum_name(status), _enum_name(status)[:1].upper())


def _name_only(files: list[DiffFileEntry], separator: bytes) -> bytes:
    out = bytearray()
    for entry in files:
        out.extend(_primary_path(entry).encode())
        out.extend(separator)
    return bytes(out)


def _name_status(files: list[DiffFileEntry], separator: bytes) -> bytes:
    field_separator = b"\0" if separator == b"\0" else b"\t"
    out = bytearray()
    for entry in files:
        letter = _status_letter(entry.status)
        if entry.status in (DiffStatus.renamed, DiffStatus.copied):
            out.extend(f"{letter}{entry.similarity or 0:03}".encode())
            out.extend(field_separator)
            out.extend((entry.old_path or "").encode())
            out.extend(field_separator)
            out.extend((entry.new_path or "").encode())
            out.extend(separator)
        else:
            out.extend(letter.encode())
            out.extend(field_separator)
            out.extend(_primary_path(entry).encode())
            out.extend(separator)
    return bytes(out)


def _numstat(files: list[DiffFileEntry], separator: bytes) -> bytes:
    out = bytearray()
    for entry in files:
        if entry.is_binary:
            added, deleted = "-", "-"
        else:
            added = str(entry.insertions or 0)
            deleted = str(entry.deletions or 0)
        out.extend(f"{added}\t{deleted}\t".encode())
        out.extend(_primary_path(entry).encode())
        out.extend(separator)
    return bytes(out)


def _stat(files: list[DiffFileEntry]) -> bytes:
    out = []
    total_insertions = 0
    total_deletions = 0
    for entry in files:
        insertions = entry.insertions or 0
        deletions = entry.deletions or 0
        total_insertions += insertions
        total_deletions += deletions
        changes = insertions + deletions
        bar = "+" * max(insertions, 0) + "-" * max(deletions, 0)
        out.append(f" {_primary_path(entry)} | {changes} {bar}\n")
    out.append(_shortstat_line(len(files), total_insertions, total_deletions))
    return "".join(out).encode()


def _shortstat(files: list[DiffFileEntry]) -> bytes:
    total_insertions = sum(entry.insertions or 0 for entry in files)
    total_deletions = sum(entry.deletions or 0 for entry in files)
    return _shortstat_line(len(files), total_insertions, total_deletions).encode()


def _shortstat_line(files: int, insertions: int, deletions: int) -> str:
    if files == 0:
        return ""

    def plural(count: int, singular: str) -> str:
        return singular if count == 1 else f"{singular}s"

    parts = [f" {files} {plural(files, 'file')} changed"]
    if insertions > 0:
        parts.append(f"{insertions} {plural(insertions, 'insertion')}(+)")
    if deletions > 0:
        parts.append(f"{deletions} {plural(deletions, 'deletion')}(-)")
    return f"{', '.join(parts)}\n"


def _summary(files: list[DiffFileEntry]) -> bytes:
    out = []
    for entry in files:
        if entry.status is DiffStatus.added:
            out.append(f" create mode {entry.new_mode or 0:06o} {_primary_path(entry)}\n")
        elif entry.status is DiffStatus.deleted:
            out.append(f" delete mode {entry.old_mode or 0:06o} {_primary_path(entry)}\n")
        elif entry.status is DiffStatus.renamed:
            out.append(
                " rename "
                f"{entry.old_path or ''} => {entry.new_path or ''} "
                f"({entry.similarity or 0}%)\n"
            )
    return "".join(out).encode()


def _file_entry_json(entry: DiffFileEntry) -> dict[str, Any]:
    return {
        "file_id": entry.file_id,
        "status": _enum_name(entry.status),
        "old_path": entry.old_path,
        "new_path": entry.new_path,
        "old_mode": entry.old_mode,
        "new_mode": entry.new_mode,
        "similarity": entry.similarity,
        "insertions": entry.insertions,
        "deletions": entry.deletions,
        "is_binary": entry.is_binary,
        "scope": _scope_json(entry.scope),
    }


def _summary_json(summary: Any) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        "has_differences": summary.has_differences,
        "repos_examined": summary.repos_examined,
        "repos_with_differences": summary.repos_with_differences,
        "files_changed": summary.files_changed,
        "insertions": summary.insertions,
        "deletions": summary.deletions,
    }


def _excluded_target_json(target: Any) -> dict[str, Any]:
    return {
        "reason": _enum_name(target.reason),
        "snapshot_id": target.snapshot_id,
        "member_id": target.scope.member_id,
        "member_path": target.scope.member_path,
        "root": target.scope.root,
        "message": target.message,
    }


def _scope_json(scope: Any) -> dict[str, Any] | None:
    if scope is None:
        return None
    return {
        "root": scope.root,
        "member_id": scope.member_id,
        "member_path": scope.member_path,
        "source_kind": _enum_name(scope.source_kind) if scope.source_kind is not None else None,
    }


def _enum_name(value: Any) -> str:
    return getattr(value, "name", str(value))


def _write_stdout_bytes(data: bytes) -> None:
    if not data:
        return
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        buffer.flush()
        return
    sys.stdout.write(data.decode("utf-8", "surrogateescape"))
    sys.stdout.flush()


def _write_stdout_text(text: str) -> None:
    if not text:
        return
    sys.stdout.write(text)
    sys.stdout.flush()


def _write_stderr_text(text: str) -> None:
    sys.stderr.write(text)
    sys.stderr.flush()
