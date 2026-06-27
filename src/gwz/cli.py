from __future__ import annotations

import argparse
import asyncio
import sys

from . import __version__
from . import cli_branch_stash, cli_local, cli_mutation, cli_read
from .cli_render import render_error, render_response
from .cli_shared import (
    CliUsageError,
    CommandContext,
    CommandRegistry,
    GwzArgumentParser,
    add_global_options,
    exit_code_for_error,
    exit_code_for_response,
    global_options_parent,
    meta_kwargs,
    validate_args,
)
from .client import Client
from .errors import GwzError, GwzOperationError


def build_parser() -> argparse.ArgumentParser:
    parser = GwzArgumentParser(
        prog="gwz-py",
        description="Manage GWZ multi-repository workspaces",
    )
    add_global_options(parser)
    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    registry = CommandRegistry()
    register_commands(registry)
    registry.attach_to(
        subparsers,
        global_parent_factory=lambda: global_options_parent("_cmd_"),
    )
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
        try:
            response = await handler(context)
        except GwzOperationError as exc:
            response = _renderable_operation_response(args, exc)
            if response is None:
                raise

    rendered = render_response(
        response,
        json_mode=args.json or getattr(args, "jsonl", False),
        local_paths=getattr(args, "local", False),
        porcelain=getattr(args, "porcelain", False),
    )
    if rendered:
        print(rendered)
    return _exit_code_for_cli_response(args, response)


def _renderable_operation_response(args: argparse.Namespace, exc: GwzOperationError) -> object | None:
    response = exc.response
    if (
        getattr(args, "command", None) == "status"
        and response is not None
        and getattr(response, "workspace_git_status", None) is not None
    ):
        return response
    return None


def _exit_code_for_cli_response(args: argparse.Namespace, response: object) -> int:
    if (
        getattr(args, "command", None) == "status"
        and getattr(response, "workspace_git_status", None) is not None
    ):
        return 0
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
