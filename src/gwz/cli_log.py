from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cli_shared import (
    CliUsageError,
    CommandContext,
    CommandRegistry,
)


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
        help="Include commit message bodies in the core result",
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

    # S3.5 owns record delivery and lifecycle, not rendering. Draining here
    # exercises the exact stream S3.6 will render without creating a temporary
    # output format or retaining an unbounded history in Python memory.
    async for _record in context.client.log_output(response.output):
        pass
    return LogCliResult(exit_code=exit_code_for_log_response(response))


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
