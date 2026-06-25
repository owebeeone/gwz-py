from __future__ import annotations

import argparse
from typing import Any

from .cli_shared import CliUsageError, CommandContext, CommandRegistry


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
        "repo",
        help="Manage workspace repositories",
        configure=configure_repo,
        handler=handle_repo,
    )


def configure_status(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--combined", action="store_true", help="Include combined workspace status")
    parser.add_argument("--no-combined", action="store_true", help="Render per-repo status")
    parser.add_argument("--no-files", action="store_true", help="Omit file changes from combined status")
    parser.add_argument("--no-branches", action="store_true", help="Omit branch summaries from combined status")


async def handle_status(context: CommandContext) -> Any:
    if context.args.combined and context.args.no_combined:
        raise CliUsageError("--combined and --no-combined are mutually exclusive")
    if context.args.no_files and context.args.no_branches:
        raise CliUsageError("--no-files and --no-branches cannot both be supplied")
    combined = not context.args.no_combined
    return await context.client.status(
        combined=combined,
        include_file_changes=False if context.args.no_files else None,
        include_branch_summary=False if context.args.no_branches else None,
        **context.meta,
    )


def configure_ls(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--local", action="store_true", help="Print workspace-relative paths")
    parser.add_argument(
        "--unmaterialized",
        action="store_true",
        help="Include configured-but-unmaterialized members",
    )
    parser.add_argument(
        "--materialized-only",
        action="store_true",
        help="Hide configured but missing members",
    )


async def handle_ls(context: CommandContext) -> Any:
    if context.args.unmaterialized and context.args.materialized_only:
        raise CliUsageError("--unmaterialized and --materialized-only are mutually exclusive")
    include_unmaterialized = True if context.args.unmaterialized else not context.args.materialized_only
    return await context.client.ls(
        include_unmaterialized=include_unmaterialized,
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


def configure_repo(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="repo_command", required=True)

    add = subparsers.add_parser("add", help="Add an existing git repository as a member")
    add.add_argument("repo_path", help="Path to an existing local git repository")

    create = subparsers.add_parser("create", help="Create a new repository member")
    create.add_argument("member_path", help="Workspace-relative path for the new repository member")

    sync = subparsers.add_parser("sync", help="Refresh member metadata from local git config")
    sync.add_argument("member_path", nargs="?", help="Workspace-relative member path to sync")


async def handle_repo(context: CommandContext) -> Any:
    if context.args.repo_command == "add":
        return await context.client.add_existing_repo(
            context.args.repo_path,
            **context.meta,
        )
    if context.args.repo_command == "create":
        return await context.client.create_repo(
            context.args.member_path,
            **context.meta,
        )
    if context.args.repo_command == "sync":
        if context.args.member_path and any(
            key in context.meta for key in ("all_members", "member_ids", "paths")
        ):
            raise CliUsageError("repo sync member path cannot be combined with global selection")
        return await context.client.repo_sync(
            context.args.member_path,
            **context.meta,
        )
    raise AssertionError(context.args.repo_command)
