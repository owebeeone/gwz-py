from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any

from .cli_render import (
    log_color_enabled,
    render_log_degradation,
    render_log_entry,
    render_log_record_json,
)
from .cli_shared import (
    CliUsageError,
    CommandContext,
    CommandRegistry,
)
from .errors import GwzBridgeError
from .protocol.generated import LogOutputRecordKind


@dataclass(frozen=True, slots=True)
class LogCliResult:
    exit_code: int


def is_log_result(value: object) -> bool:
    return isinstance(value, LogCliResult)


class _StoreOnce(argparse.Action):
    """Match clap's refusal of repeated single-value log options."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        seen_attr = f"_gwz_log_seen_{self.dest}"
        if getattr(namespace, seen_attr, False):
            parser.error(f"argument {option_string}: cannot be used multiple times")
        setattr(namespace, seen_attr, True)
        setattr(namespace, self.dest, values)


class _StoreTrueOnce(argparse.Action):
    """Store a singleton boolean while rejecting Clap-invalid repetition."""

    def __init__(self, option_strings: list[str], dest: str, **kwargs: Any) -> None:
        super().__init__(option_strings, dest, nargs=0, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        seen_attr = f"_gwz_log_seen_{self.dest}"
        if getattr(namespace, seen_attr, False):
            parser.error(f"argument {option_string}: cannot be used multiple times")
        setattr(namespace, seen_attr, True)
        setattr(namespace, self.dest, True)


def _non_negative_i64(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    if parsed > (1 << 63) - 1:
        raise argparse.ArgumentTypeError("must fit in a signed 64-bit integer")
    return parsed


def register_commands(registry: CommandRegistry) -> None:
    registry.register(
        "log",
        help="Show unified workspace commit history",
        configure=configure_log,
        handler=handle_log,
    )


def configure_log(parser: argparse.ArgumentParser) -> None:
    parser.allow_abbrev = False
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.description = """\
Show local commit history from the workspace root and selected members as one
newest-first stream. The compact form shows workspace-relative member paths;
--full uses git-style blocks with a complete member table.

Members that cannot contribute are summarized on stderr. Human text is lossy
and terminal controls are sanitized. Output does not use a pager; color auto
keys only on whether stdout is a terminal."""
    parser.epilog = """\
Examples:
  gwz-py log
  gwz-py log --full --body
  gwz-py --target mem_api log main..topic -- src"""
    limit = parser.add_mutually_exclusive_group()
    limit.add_argument(
        "-n",
        dest="max_entries",
        type=_non_negative_i64,
        action=_StoreOnce,
        metavar="n",
        help="Limit the global result to N entries (0 disables the limit)",
    )
    limit.add_argument(
        "--no-limit",
        action=_StoreTrueOnce,
        default=False,
        help="Disable the global result limit",
    )
    parser.add_argument(
        "--since",
        action=_StoreOnce,
        metavar="time",
        help=(
            "Include commits at or after TIME (RFC3339/ISO-8601; date-only is "
            "local midnight, offset-less is local, or use @epoch-seconds)"
        ),
    )
    parser.add_argument(
        "--until",
        action=_StoreOnce,
        metavar="time",
        help=(
            "Include commits at or before TIME (RFC3339/ISO-8601; date-only is "
            "local midnight, offset-less is local, or use @epoch-seconds)"
        ),
    )
    parser.add_argument(
        "--author",
        action=_StoreOnce,
        metavar="regex",
        help=(
            "Match a case-sensitive Rust regex (not Git regex syntax) against "
            "Name <email>"
        ),
    )
    parser.add_argument(
        "--grep",
        action=_StoreOnce,
        metavar="regex",
        help=(
            "Match a case-sensitive Rust regex (not Git regex syntax) against "
            "the full raw commit message"
        ),
    )
    parser.add_argument(
        "--no-merges",
        action=_StoreTrueOnce,
        default=False,
        help="Exclude merge commits before workspace coalescing",
    )
    parser.add_argument(
        "--first-parent",
        action=_StoreTrueOnce,
        default=False,
        help="Follow only each commit's first parent",
    )
    parser.add_argument(
        "--strict",
        action=_StoreTrueOnce,
        default=False,
        help="Promote any selected-repository degradation to failure",
    )
    parser.add_argument(
        "--no-coalesce",
        action=_StoreTrueOnce,
        default=False,
        help="Disable workspace-level commit coalescing",
    )
    parser.add_argument(
        "--body",
        action=_StoreTrueOnce,
        default=False,
        help="Include commit message bodies in --full and machine output",
    )
    parser.add_argument(
        "--full",
        action=_StoreTrueOnce,
        default=False,
        help="Render git-style blocks with a complete member table",
    )
    parser.add_argument(
        "--tagged",
        action=_StoreTrueOnce,
        default=False,
        help="Select only repositories containing every supplied local tag",
    )
    parser.add_argument(
        "--color",
        choices=("always", "never", "auto"),
        default="auto",
        action=_StoreOnce,
        metavar="when",
        help="Colorize output: always, never, or auto",
    )
    parser.add_argument(
        "operands",
        nargs="*",
        help="Revisions, ranges, or +snapshot ids; put pathspecs after --",
    )
    parser.set_defaults(pathspecs=[])


async def handle_log(context: CommandContext) -> LogCliResult:
    args = context.args
    response = await context.client.log(
        list(getattr(args, "operands", []) or []),
        pathspecs=list(getattr(args, "pathspecs", []) or []),
        workspace_cwd=_workspace_relative_cwd(args.root),
        max_entries=0 if args.no_limit else args.max_entries,
        since=args.since,
        until=args.until,
        author=args.author,
        grep=args.grep,
        no_merges=True if args.no_merges else None,
        first_parent=True if args.first_parent else None,
        strict=True if args.strict else None,
        coalesce=False if args.no_coalesce else None,
        include_body=True if args.body else None,
        tagged=True if args.tagged else None,
        **context.meta,
    )

    exit_code = exit_code_for_log_response(response)
    machine = bool(args.json or args.jsonl)
    if machine:
        prefix = (
            '{"record":"header","schema":"gwz.log/v0"}\n'
            if args.jsonl
            else '{"records":['
        )
        try:
            _write_and_flush(sys.stdout, prefix)
        except BrokenPipeError:
            await context.client._release_log_output(response.output)
            return LogCliResult(exit_code=0)
        except OSError as error:
            await context.client._release_log_output(response.output)
            raise _output_error("stdout", error) from error

    records = context.client.log_output(response.output)
    first_json_record = True
    color = log_color_enabled(args.color, _stdout_is_tty())
    try:
        async for record in records:
            if machine:
                serialized = render_log_record_json(record)
                if args.jsonl:
                    chunk = serialized + "\n"
                else:
                    chunk = serialized if first_json_record else "," + serialized
                _write_and_flush(sys.stdout, chunk)
                first_json_record = False
                continue

            if (
                record.kind is LogOutputRecordKind.entry
                and record.entry is not None
                and record.degradation is None
            ):
                rendered = render_log_entry(
                    record.entry,
                    full=args.full,
                    color=color,
                )
                _write_and_flush(
                    sys.stdout,
                    rendered + ("\n\n" if args.full else "\n"),
                )
            elif (
                record.kind is LogOutputRecordKind.degradation
                and record.entry is None
                and record.degradation is not None
            ):
                try:
                    _write_and_flush(
                        sys.stderr,
                        render_log_degradation(record.degradation, color=color)
                        + "\n",
                    )
                except OSError as error:
                    raise _output_error("stderr", error) from error
            else:
                raise GwzBridgeError(
                    "commit-log output record kind does not match its payload",
                    code="InternalError",
                )
        if args.json:
            _write_and_flush(sys.stdout, '],"schema":"gwz.log/v0"}\n')
    except BrokenPipeError:
        return LogCliResult(exit_code=0)
    except OSError as error:
        raise _output_error("output", error) from error
    finally:
        close = getattr(records, "aclose", None)
        if close is not None:
            await close()
    return LogCliResult(exit_code=exit_code)


def _write_and_flush(stream: Any, value: str) -> None:
    byte_stream = getattr(stream, "buffer", None)
    if byte_stream is not None:
        byte_stream.write(value.encode("utf-8"))
        byte_stream.flush()
    else:
        stream.write(value)
        stream.flush()


def _stdout_is_tty() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except (AttributeError, OSError):
        return False


def _output_error(channel: str, error: OSError) -> GwzBridgeError:
    return GwzBridgeError(
        f"cannot write log {channel}: {error}",
        code="IoError",
    )


def exit_code_for_log_response(response: object) -> int:
    envelope = getattr(response, "response", None)
    meta = getattr(envelope, "meta", None)
    aggregate = getattr(meta, "aggregate_status", None)
    name = getattr(aggregate, "name", str(aggregate))
    if name in {"accepted", "ok", "noop", "dirty"}:
        return 0
    if name == "rejected":
        return 2
    return 1


def exit_code_for_log_error(error: BaseException) -> int:
    if isinstance(error, CliUsageError):
        return 2
    code = getattr(error, "code", None)
    return 1 if code in _EXECUTION_ERROR_CODES or code is None else 2


_EXECUTION_ERROR_CODES = frozenset(
    {
        "IoError",
        "InternalError",
        "GitCommandFailed",
        "ExternalToolMissing",
        "RemoteRejected",
    }
)


def _workspace_relative_cwd(root: str | None) -> str:
    cwd = Path.cwd().resolve()
    workspace = Path(root).resolve() if root is not None else cwd
    try:
        relative = cwd.relative_to(workspace)
    except ValueError:
        return ""
    return "" if relative == Path(".") else relative.as_posix()
