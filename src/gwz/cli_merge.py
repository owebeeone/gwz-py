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
from .protocol.generated import MergeMode, MergeOp


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
        "gwz-py merge [source] [--dry-run] [--status | --continue | --abort]"
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
        action="store_true",
        help="Inspect coordinated merge state without changing it",
    )
    parser.add_argument("--preserve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--gc", nargs="?", const="", help=argparse.SUPPRESS)
    parser.add_argument("--ff-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-ff", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("-m", "--message", help=argparse.SUPPRESS)


async def handle_merge(context: CommandContext) -> Any:
    lifecycle_ops = sum(
        (
            context.args.resume,
            context.args.abort,
            context.args.status,
            context.args.gc is not None,
        )
    )
    if lifecycle_ops > 1:
        raise CliUsageError("merge accepts only one lifecycle operation", code="InvalidRequest")
    if context.args.ff_only and context.args.no_ff:
        raise CliUsageError(
            "--ff-only and --no-ff are mutually exclusive", code="InvalidRequest"
        )
    op = (
        MergeOp.resume
        if context.args.resume
        else MergeOp.abort
        if context.args.abort
        else MergeOp.status
        if context.args.status
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
        "merge_id": context.args.gc or None,
        "mode": mode,
        "message": context.args.message,
        "preserve": True if context.args.preserve else None,
        **context.meta,
    }
    if context.args.jsonl:
        handle = await context.client.merge_stream(*merge_args, **merge_kwargs)
        async for event in handle.events():
            _write_jsonl(operation_event_json(event))
        response = await handle.result()
        _write_stdout(render_response(response, json_mode=True) + "\n")
        return MergeCliResult(exit_code=exit_code_for_response(response))
    return await context.client.merge(
        *merge_args,
        **merge_kwargs,
    )


def _write_jsonl(value: dict[str, Any]) -> None:
    _write_stdout(json.dumps(value, sort_keys=True) + "\n")


def _write_stdout(value: str) -> None:
    sys.stdout.write(value)
    sys.stdout.flush()
