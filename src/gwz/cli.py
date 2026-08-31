from __future__ import annotations

import argparse
import asyncio
import sys

from . import __version__
from . import (
    cli_branch_stash,
    cli_diff,
    cli_local,
    cli_log,
    cli_merge,
    cli_mutation,
    cli_read,
)
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
    _silence_broken_stdout,
    _is_broken_pipe,
    validate_args,
)
from .client import Client
from .errors import GwzError, GwzOperationError
from .protocol.generated import MergeResponse


def build_parser() -> argparse.ArgumentParser:
    parser = GwzArgumentParser(
        prog="gwz-py",
        description="Manage GWZ multi-repository workspaces",
        allow_abbrev=False,
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
    cli_diff.register_commands(registry)
    cli_log.register_commands(registry)
    cli_mutation.register_commands(registry)
    cli_branch_stash.register_commands(registry)
    cli_merge.register_commands(registry)
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

    if cli_diff.is_diff_result(response):
        return response.exit_code
    if cli_log.is_log_result(response):
        return response.exit_code
    if cli_merge.is_merge_result(response):
        return response.exit_code

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
    if isinstance(response, MergeResponse):
        return response
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
        machine = args.json or getattr(args, "jsonl", False)
        if getattr(args, "command", None) == "log":
            return _write_log_error(exc, machine=machine)
        print(render_error(exc, json_mode=machine), file=sys.stdout if machine else sys.stderr)
        return exit_code_for_error(exc)


def _write_log_error(error: BaseException, *, machine: bool) -> int:
    stream = sys.stdout if machine else sys.stderr
    try:
        cli_log._write_and_flush(stream, render_error(error, json_mode=machine) + "\n")
    except OSError as write_error:
        if machine and _is_broken_pipe(write_error):
            _silence_broken_stdout(stream)
            return 0
        return 1
    return cli_log.exit_code_for_log_error(error)


if __name__ == "__main__":
    raise SystemExit(main())
