from __future__ import annotations

import argparse
from typing import Any

from .cli_shared import CliUsageError, CommandContext, CommandRegistry, global_options_parent
from .protocol.generated import BranchOp, StashOp


def register_commands(registry: CommandRegistry) -> None:
    registry.register(
        "branch",
        help="Manage git branches across workspace members",
        configure=configure_branch,
        handler=handle_branch,
    )
    registry.register(
        "stash",
        help="Manage coordinated git stashes across workspace members",
        configure=configure_stash,
        handler=handle_stash,
    )


def configure_branch(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--list", action="store_true", help="List branches")
    parser.add_argument("--create", metavar="name", help="Create a branch")
    parser.add_argument("--from", dest="start_ref", metavar="ref", help="Start point for --create")
    parser.add_argument("--switch", action="store_true", help="Switch after --create")
    parser.add_argument("--delete", metavar="name", help="Delete a branch")
    parser.add_argument("--merge", metavar="ref", help="Merge a source ref")


async def handle_branch(context: CommandContext) -> Any:
    operations = [
        context.args.list,
        context.args.create is not None,
        context.args.delete is not None,
        context.args.merge is not None,
    ]
    if sum(1 for operation in operations if operation) > 1:
        raise CliUsageError("branch accepts only one of --list, --create, --delete, or --merge")
    if context.args.switch and context.args.create is None:
        raise CliUsageError("--switch requires --create")
    if context.args.start_ref is not None and context.args.create is None:
        raise CliUsageError("--from requires --create")

    if context.args.create is not None:
        return await context.client.branch(
            context.args.create,
            op=BranchOp.create,
            start_ref=context.args.start_ref or "HEAD",
            switch_after_create=True if context.args.switch else None,
            **context.meta,
        )
    if context.args.delete is not None:
        return await context.client.branch(
            context.args.delete,
            op=BranchOp.delete,
            **context.meta,
        )
    if context.args.merge is not None:
        return await context.client.branch(
            None,
            op=BranchOp.merge,
            start_ref=context.args.merge,
            **context.meta,
        )
    return await context.client.branch(op=BranchOp.list, **context.meta)


def configure_stash(parser: argparse.ArgumentParser) -> None:
    nested_global = global_options_parent("_nested_")
    subparsers = parser.add_subparsers(dest="stash_command", required=True)

    push = subparsers.add_parser(
        "push",
        help="Push a coordinated stash",
        parents=[nested_global],
        conflict_handler="resolve",
    )
    push.add_argument("-u", dest="include_untracked", action="store_true", help="Include untracked files")
    push.add_argument("-a", dest="include_ignored", action="store_true", help="Include ignored files")
    push.add_argument("-m", "--message", help="Message suffix")

    list_ = subparsers.add_parser(
        "list",
        help="List coordinated stashes",
        parents=[nested_global],
        conflict_handler="resolve",
    )
    list_.add_argument("--expanded", action="store_true", help="Include expanded bundle detail")

    apply = subparsers.add_parser(
        "apply",
        help="Apply a coordinated stash",
        parents=[nested_global],
        conflict_handler="resolve",
    )
    apply.add_argument("stash_id", nargs="?", help="Stash id; defaults to latest")

    pop = subparsers.add_parser(
        "pop",
        help="Pop a coordinated stash",
        parents=[nested_global],
        conflict_handler="resolve",
    )
    pop.add_argument("stash_id", nargs="?", help="Stash id; defaults to latest")

    drop = subparsers.add_parser(
        "drop",
        help="Drop a coordinated stash",
        parents=[nested_global],
        conflict_handler="resolve",
    )
    drop.add_argument("stash_id", help="Stash id")


async def handle_stash(context: CommandContext) -> Any:
    command = context.args.stash_command
    if command == "push":
        if context.args.include_untracked and context.args.include_ignored:
            raise CliUsageError("-u and -a are mutually exclusive")
        return await context.client.stash(
            op=StashOp.push,
            message=context.args.message,
            include_untracked=True if context.args.include_untracked else None,
            include_ignored=True if context.args.include_ignored else None,
            **context.meta,
        )
    if command == "list":
        return await context.client.stash(
            op=StashOp.list,
            expanded=True if context.args.expanded else None,
            **context.meta,
        )
    if command == "apply":
        return await context.client.stash(
            op=StashOp.apply,
            stash_id=context.args.stash_id,
            **context.meta,
        )
    if command == "pop":
        return await context.client.stash(
            op=StashOp.pop,
            stash_id=context.args.stash_id,
            **context.meta,
        )
    if command == "drop":
        return await context.client.stash(
            op=StashOp.drop,
            stash_id=context.args.stash_id,
            **context.meta,
        )
    raise AssertionError(command)
