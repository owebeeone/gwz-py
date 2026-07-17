from __future__ import annotations

import argparse
from typing import Any

from .cli_shared import CliUsageError, CommandContext, CommandRegistry
from .protocol.generated import MergeMode, MergeOp


def register_commands(registry: CommandRegistry) -> None:
    registry.register(
        "merge",
        help="Merge a source ref across selected workspace members",
        configure=configure_merge,
        handler=handle_merge,
    )


def configure_merge(parser: argparse.ArgumentParser) -> None:
    parser.usage = "gwz-py merge <source> [--dry-run]"
    parser.add_argument(
        "source",
        nargs="?",
        help="Source ref resolved independently in each selected member",
    )
    parser.add_argument("--continue", dest="resume", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--abort", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--status", action="store_true", help=argparse.SUPPRESS)
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
    return await context.client.merge(
        context.args.source,
        op=op,
        merge_id=context.args.gc or None,
        mode=mode,
        message=context.args.message,
        preserve=True if context.args.preserve else None,
        **context.meta,
    )
