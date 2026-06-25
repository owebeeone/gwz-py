from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from .cli_render import render_error, render_response
from .cli_shared import (
    CliUsageError,
    CommandContext,
    CommandRegistry,
    add_global_options,
    exit_code_for_error,
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
    registry.register(
        "status",
        help="Show workspace status",
        configure=configure_status,
        handler=handle_status,
    )
    registry.register(
        "ls",
        help="List workspace members",
        configure=configure_ls,
        handler=handle_ls,
    )
    registry.register(
        "init",
        help="Create or initialize a workspace",
        configure=configure_init,
        handler=handle_init,
    )
    registry.register(
        "materialize",
        help="Materialize workspace members",
        configure=configure_materialize,
        handler=handle_materialize,
    )


def configure_status(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--combined", action="store_true", help="Include combined workspace status")


async def handle_status(context: CommandContext) -> Any:
    return await context.client.status(combined=context.args.combined, **context.meta)


def configure_ls(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--materialized-only",
        action="store_true",
        help="Hide configured but missing members",
    )


async def handle_ls(context: CommandContext) -> Any:
    return await context.client.ls(
        include_unmaterialized=not context.args.materialized_only,
        **context.meta,
    )


def configure_init(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("urls", nargs="*", help="Initial source URLs")
    parser.add_argument("--path", default="", help="Workspace root or source path prefix")


async def handle_init(context: CommandContext) -> Any:
    if context.args.urls:
        return await context.client.init_from_sources(
            context.args.urls,
            workspace_root=context.args.path,
            **context.meta,
        )
    return await context.client.create_workspace(
        context.args.path or context.args.root,
        **context.meta,
    )


def configure_materialize(parser: argparse.ArgumentParser) -> None:
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--lock", action="store_true", help="Materialize the workspace lock")
    target.add_argument("--head", action="store_true", help="Materialize repository heads")
    target.add_argument("--snapshot", help="Materialize a workspace snapshot")
    target.add_argument("--tag", help="Materialize a workspace tag")


async def handle_materialize(context: CommandContext) -> Any:
    if context.args.head:
        return await context.client.materialize("head", **context.meta)
    if context.args.snapshot:
        return await context.client.materialize(
            "snapshot",
            name=context.args.snapshot,
            **context.meta,
        )
    if context.args.tag:
        return await context.client.materialize("tag", name=context.args.tag, **context.meta)
    return await context.client.materialize("lock", **context.meta)


async def run(args: argparse.Namespace) -> int:
    validate_args(args)
    handler = getattr(args, "command_handler")
    async with Client(root=args.root) as client:
        context = CommandContext(args=args, client=client, meta=meta_kwargs(args))
        response = await handler(context)

    rendered = render_response(response, json_mode=args.json)
    if rendered:
        print(rendered)
    return 0


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
