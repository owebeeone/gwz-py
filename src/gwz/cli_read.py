from __future__ import annotations

import argparse
from typing import Any

from . import cli_local
from .cli_shared import CliUsageError, CommandContext, CommandRegistry, global_options_parent
from .errors import GwzBridgeError
from .protocol.generated import CloneRepoMemberResponse


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
    parser.add_argument("--porcelain", action="store_true", help="Render porcelain output")
    parser.add_argument("--no-files", action="store_true", help="Omit file changes from combined status")
    parser.add_argument("--no-branches", action="store_true", help="Omit branch summaries from combined status")


async def handle_status(context: CommandContext) -> Any:
    if context.args.porcelain and (context.args.json or context.args.jsonl):
        raise CliUsageError("--porcelain cannot be combined with --json or --jsonl")
    if context.args.porcelain and context.args.no_combined:
        raise CliUsageError("--porcelain cannot be combined with --no-combined")
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
    nested_global = global_options_parent("_nested_")
    subparsers = parser.add_subparsers(dest="repo_command", required=True)

    add = subparsers.add_parser(
        "add",
        help="Add an existing git repository as a member",
        parents=[nested_global],
        conflict_handler="resolve",
    )
    add.add_argument("repo_path", help="Path to an existing local git repository")
    _add_identity_options(add)

    clone = subparsers.add_parser(
        "clone",
        help="Clone and register a new repository member",
        parents=[nested_global],
        conflict_handler="resolve",
    )
    clone.add_argument("url", help="Git URL of the repository to clone")
    clone.add_argument(
        "member_path",
        nargs="?",
        help="Workspace-relative target path; defaults from the URL",
    )
    _add_identity_options(clone)

    create = subparsers.add_parser(
        "create",
        help="Create a new repository member",
        parents=[nested_global],
        conflict_handler="resolve",
    )
    create.add_argument(
        "member_path", help="Workspace-relative path for the new repository member"
    )
    _add_identity_options(create)

    detach = subparsers.add_parser(
        "detach",
        help="Detach a repository member without deleting its checkout",
        parents=[nested_global],
        conflict_handler="resolve",
    )
    detach.add_argument("member", help="Active member id or workspace-relative path")

    attach = subparsers.add_parser(
        "attach",
        help="Reattach an inactive repository designation",
        parents=[nested_global],
        conflict_handler="resolve",
    )
    attach.add_argument("member_id", help="Inactive member designation id")

    sync = subparsers.add_parser(
        "sync",
        help="Refresh member metadata from local git config",
        parents=[nested_global],
        conflict_handler="resolve",
    )
    sync.add_argument(
        "member_path", nargs="?", help="Workspace-relative member path to sync"
    )


def _add_identity_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--member-id", help="Explicit member designation id")
    parser.add_argument("--source-id", help="Explicit logical source id")


async def handle_repo(context: CommandContext) -> Any:
    if context.args.repo_command == "add":
        return await context.client.add_existing_repo(
            context.args.repo_path,
            member_id=context.args.member_id,
            source_id=context.args.source_id,
            **context.meta,
        )
    if context.args.repo_command == "clone":
        return await _handle_repo_clone(context)
    if context.args.repo_command == "create":
        return await context.client.create_repo(
            context.args.member_path,
            member_id=context.args.member_id,
            source_id=context.args.source_id,
            **context.meta,
        )
    if context.args.repo_command == "detach":
        _reject_repo_operand_with_selection(context, "repo detach")
        return await context.client.detach_repo_member(
            context.args.member,
            **context.meta,
        )
    if context.args.repo_command == "attach":
        _validate_member_id(context.args.member_id)
        _reject_repo_operand_with_selection(context, "repo attach")
        return await context.client.attach_repo_member(
            context.args.member_id,
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


async def _handle_repo_clone(context: CommandContext) -> Any:
    call_kwargs = {
        "member_id": context.args.member_id,
        "source_id": context.args.source_id,
        **context.meta,
    }
    if context.args.json or context.args.jsonl or context.meta.get("dry_run"):
        return await context.client.clone_repo_member(
            context.args.url,
            context.args.member_path,
            **call_kwargs,
        )

    operation_id = None
    async for event in context.client.clone_repo_member_stream(
        context.args.url,
        context.args.member_path,
        **call_kwargs,
    ):
        operation_id = event.operation_id
        cli_local.render_clone_event(event)
    if operation_id is None:
        raise GwzBridgeError("repo clone stream completed without an operation event")
    result = await context.client.operation_result(operation_id)
    return CloneRepoMemberResponse(
        response=cli_local.response_envelope_from_result(result)
    )


def _reject_repo_operand_with_selection(context: CommandContext, command: str) -> None:
    if any(
        key in context.meta
        for key in ("all_members", "member_ids", "paths", "targets", "exclude_targets")
    ):
        raise CliUsageError(f"{command} member cannot be combined with global selection")


def _validate_member_id(value: str) -> None:
    suffix = value.removeprefix("mem_")
    if (
        not value.startswith("mem_")
        or not suffix
        or not all(
            character.isascii() and (character.isalnum() or character in "_-.")
            for character in value
        )
    ):
        raise CliUsageError(
            "member id must start with mem_ and contain only portable characters"
        )
