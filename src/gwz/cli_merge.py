from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any

from .cli_render import operation_event_json, render_response
from .cli_shared import (
    CliUsageError,
    CommandContext,
    CommandRegistry,
    exit_code_for_response,
)
from .protocol.generated import EventKind, MergeMode, MergeOp, Severity


@dataclass(frozen=True, slots=True)
class MergeCliResult:
    exit_code: int


def is_merge_result(value: object) -> bool:
    return isinstance(value, MergeCliResult)


def register_commands(registry: CommandRegistry) -> None:
    registry.register(
        "merge",
        help="Merge a source ref across selected workspace repositories",
        configure=configure_merge,
        handler=handle_merge,
    )


def configure_merge(parser: argparse.ArgumentParser) -> None:
    parser.usage = (
        "gwz-py merge [source] [--dry-run] [--ff-only] [-m <message>]\n"
        "       gwz-py merge --status [merge-id]\n"
        "       gwz-py merge --continue\n"
        "       gwz-py merge --abort [--preserve]\n"
        "       gwz-py merge --gc [merge-id]"
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="Source ref resolved independently in each selected repository",
    )
    parser.add_argument(
        "--continue",
        dest="resume",
        action="store_true",
        help="Continue the open coordinated merge after resolving conflicts",
    )
    parser.add_argument(
        "--abort",
        action="store_true",
        help="Safely roll back the open coordinated merge",
    )
    parser.add_argument(
        "--status",
        nargs="?",
        const="",
        metavar="merge-id",
        help="Inspect the open merge, or a retained closed merge by id",
    )
    parser.add_argument(
        "--preserve",
        action="store_true",
        help="With --abort, preserve safe post-merge commits and local changes",
    )
    parser.add_argument(
        "--gc",
        nargs="?",
        const="",
        metavar="merge-id",
        help="Apply retention, or remove one retained merge and its backup refs",
    )
    parser.add_argument(
        "--ff-only",
        action="store_true",
        help="Require every selected repository to merge by fast-forward",
    )
    # A1 activated the v1 record lifecycle; `--no-ff` left `argparse.SUPPRESS`
    # and became a public surface, at parity with the Rust CLI's help text.
    parser.add_argument(
        "--no-ff",
        action="store_true",
        help="Always create a merge commit, even when a fast-forward is possible",
    )
    # DR-1: crash recovery is a capability, not a gate. A start on a volume that
    # cannot prove durable identity warns and continues; this flag asks core to
    # refuse instead. Start only -- core stays the authority.
    parser.add_argument(
        "--filesystem-strict",
        action="store_true",
        help="Refuse to merge when crash recovery is unsupported on this filesystem",
    )
    parser.add_argument(
        "-m", "--message", help="Use a custom merge commit-message body"
    )


async def handle_merge(context: CommandContext) -> Any:
    lifecycle_ops = sum(
        (
            context.args.resume,
            context.args.abort,
            context.args.status is not None,
            context.args.gc is not None,
        )
    )
    if lifecycle_ops > 1:
        raise CliUsageError("merge accepts only one lifecycle operation", code="InvalidRequest")
    if context.args.ff_only and context.args.no_ff:
        raise CliUsageError(
            "--ff-only and --no-ff are mutually exclusive", code="InvalidRequest"
        )
    # The crash-recovery decision belongs to the start that opens the attempt;
    # a later lifecycle op uses what that start opened and never consults the
    # flag, so offering it there is a request error, not a silent no-op.
    if context.args.filesystem_strict and lifecycle_ops > 0:
        raise CliUsageError(
            "--filesystem-strict is accepted only when starting a merge",
            code="InvalidRequest",
        )
    op = (
        MergeOp.resume
        if context.args.resume
        else MergeOp.abort
        if context.args.abort
        else MergeOp.status
        if context.args.status is not None
        else MergeOp.gc
        if context.args.gc is not None
        else MergeOp.start
    )
    mode = (
        MergeMode.ff_only
        if context.args.ff_only
        else MergeMode.no_ff
        if context.args.no_ff
        else None
    )
    merge_args = (context.args.source,)
    merge_kwargs = {
        "op": op,
        "merge_id": context.args.status or context.args.gc or None,
        "mode": mode,
        "message": context.args.message,
        "preserve": True if context.args.preserve else None,
        "filesystem_strict": True if context.args.filesystem_strict else None,
        **context.meta,
    }
    if context.args.jsonl:
        handle = await context.client.merge_stream(*merge_args, **merge_kwargs)
        async for event in handle.events():
            _write_jsonl(operation_event_json(event))
        response = await handle.result()
        _write_stdout(render_response(response, json_mode=True) + "\n")
        return MergeCliResult(exit_code=exit_code_for_response(response))
    if not _human_output(context.args):
        # Json and porcelain read the response's `crash_recovery`, exactly as
        # the Rust CLI's `NullSink` leaves them to (charter §3.5). Nothing to
        # stream for, so the plain call stands unchanged.
        return await context.client.merge(*merge_args, **merge_kwargs)
    # DR-1 §3.5: core has no stderr, so its diagnostics (the crash-recovery
    # warning, for one) reach a person only if this driver streams the event
    # channel. Human mode therefore submits and drains events as they arrive,
    # then renders the retained response exactly as the plain call did.
    handle = await context.client.merge_stream(*merge_args, **merge_kwargs)
    echo = DiagnosticEcho()
    async for event in handle.events():
        echo.write(event)
    return await handle.result()


def _human_output(args: argparse.Namespace) -> bool:
    return not (args.json or args.jsonl or getattr(args, "porcelain", False))


class DiagnosticEcho:
    """Prints core's warn/error diagnostics once each, as they arrive."""

    def __init__(self) -> None:
        self._printed: set[str] = set()

    def write(self, event: Any) -> None:
        line = self.line_for(event)
        if line is not None:
            print(line, file=sys.stderr)

    def line_for(self, event: Any) -> str | None:
        """The line to print, or None when the event is not an echoable
        diagnostic or its exact text was already printed this invocation."""
        if event.kind is not EventKind.diagnostic or not event.message:
            return None
        if event.severity is Severity.warn:
            label = "warning"
        elif event.severity is Severity.error:
            label = "error"
        else:
            return None
        line = f"{label}: {event.message}"
        if line in self._printed:
            return None
        self._printed.add(line)
        return line


def _write_jsonl(value: dict[str, Any]) -> None:
    _write_stdout(json.dumps(value, sort_keys=True) + "\n")


def _write_stdout(value: str) -> None:
    sys.stdout.write(value)
    sys.stdout.flush()
