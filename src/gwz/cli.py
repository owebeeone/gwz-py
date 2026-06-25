from __future__ import annotations

import argparse
import asyncio
import sys

from . import cli_branch_stash, cli_local, cli_mutation, cli_read
from .cli_render import render_error, render_response
from .cli_shared import (
    CliUsageError,
    CommandContext,
    CommandRegistry,
    add_global_options,
    exit_code_for_error,
    exit_code_for_response,
    meta_kwargs,
    validate_args,
)
from .client import Client
from .errors import GwzError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gwz",
        description="Manage GWZ multi-repository workspaces",
    )
    add_global_options(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)
    registry = CommandRegistry()
    register_commands(registry)
    registry.attach_to(subparsers)
    return parser


def register_commands(registry: CommandRegistry) -> None:
    cli_read.register_commands(registry)
    cli_mutation.register_commands(registry)
    cli_branch_stash.register_commands(registry)
    cli_local.register_commands(registry)


async def run(args: argparse.Namespace) -> int:
    validate_args(args)
    handler = getattr(args, "command_handler")
    async with Client(root=args.root) as client:
        context = CommandContext(args=args, client=client, meta=meta_kwargs(args))
        response = await handler(context)

    rendered = render_response(response, json_mode=args.json)
    if rendered:
        print(rendered)
    return exit_code_for_response(response)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except (CliUsageError, GwzError) as exc:
        print(render_error(exc), file=sys.stderr)
        return exit_code_for_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
