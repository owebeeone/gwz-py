from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .cli_shared import CliUsageError, CommandContext, CommandRegistry
from .protocol.generated import SnapshotSource, SnapshotSourceKind, TagOp


def register_commands(registry: CommandRegistry) -> None:
    registry.register(
        "materialize",
        help="Materialize workspace members",
        configure=configure_materialize,
        handler=handle_materialize,
    )
    registry.register(
        "snapshot",
        help="Record the current workspace selection",
        configure=configure_snapshot,
        handler=handle_snapshot,
    )
    registry.register(
        "tag",
        help="Manage git tags across workspace repos",
        configure=configure_tag,
        handler=handle_tag,
    )
    registry.register("capture", help="Record live worktree state", handler=handle_capture)
    registry.register(
        "stage",
        help="Stage file contents across workspace repos",
        configure=configure_stage,
        handler=handle_stage,
    )
    registry.register(
        "add",
        help="Alias for stage",
        configure=configure_stage,
        handler=handle_stage,
    )
    registry.register(
        "commit",
        help="Commit staged changes",
        configure=configure_commit,
        handler=handle_commit,
    )
    registry.register(
        "pull",
        help="Update workspace members to an explicit target",
        configure=configure_pull,
        handler=handle_pull,
    )
    registry.register("push", help="Push workspace member refs", handler=handle_push)


def configure_materialize(parser: argparse.ArgumentParser) -> None:
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--lock", action="store_true", help="Materialize the workspace lock")
    target.add_argument("--head", action="store_true", help="Materialize repository heads")
    target.add_argument("--snapshot", help="Materialize a workspace snapshot")
    target.add_argument("--tag", help="Materialize a workspace tag")
    target.add_argument("--switch", metavar="branch", help="Switch workspace members to a branch")


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
    if context.args.switch:
        return await context.client.materialize("branch", name=context.args.switch, **context.meta)
    return await context.client.materialize("lock", **context.meta)


class OptionalValueAction(argparse.Action):
    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: str | None,
        option_string: str | None = None,
    ) -> None:
        setattr(namespace, self.dest, values if values is not None else True)


def configure_snapshot(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("name", nargs="?", help="Snapshot name to record")
    parser.add_argument("--list", action="store_true", help="List existing snapshots")
    parser.add_argument(
        "--branch",
        nargs="?",
        action=OptionalValueAction,
        default=None,
        metavar="name",
        help="Snapshot branch heads instead of observed worktree heads",
    )


async def handle_snapshot(context: CommandContext) -> Any:
    if context.args.list:
        return await context.client.list_snapshots(**context.meta)
    if context.args.name is None:
        raise CliUsageError("snapshot requires a name")

    source = None
    if context.args.branch is True:
        source = SnapshotSource(kind=SnapshotSourceKind.current, branch=None)
    elif isinstance(context.args.branch, str):
        source = SnapshotSource(kind=SnapshotSourceKind.branch, branch=context.args.branch)

    return await context.client.snapshot(
        context.args.name,
        source=source,
        **context.meta,
    )


def configure_tag(parser: argparse.ArgumentParser) -> None:
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list", action="store_true", help="List tags")
    action.add_argument("--delete", action="store_true", help="Delete the named tag")
    action.add_argument("--push", action="store_true", help="Push tags to a remote")
    action.add_argument("--fetch", action="store_true", help="Fetch tags from a remote")
    parser.add_argument("name", nargs="?", help="Tag name")
    parser.add_argument("-m", "--message", help="Annotated tag message")
    parser.add_argument("-s", "--sign", dest="signed", action="store_true", help="Create a signed tag")


async def handle_tag(context: CommandContext) -> Any:
    if context.args.push:
        op = TagOp.push
    elif context.args.fetch:
        op = TagOp.fetch
    elif context.args.delete:
        op = TagOp.delete
    elif context.args.list or context.args.name is None:
        op = TagOp.list
    else:
        op = TagOp.create
    return await context.client.tag(
        context.args.name,
        op=op,
        message=context.args.message,
        signed=True if context.args.signed else None,
        remote=context.meta.get("remote"),
        **_meta_without(context.meta, "remote"),
    )


async def handle_capture(context: CommandContext) -> Any:
    return await context.client.capture(**context.meta)


def configure_stage(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("pathspecs", nargs="*", help="Paths to stage")
    parser.add_argument("-A", "--all", dest="stage_all", action="store_true", help="Stage all changes")


async def handle_stage(context: CommandContext) -> Any:
    return await context.client.stage(
        context.args.pathspecs,
        cwd=Path.cwd(),
        all=True if context.args.stage_all else None,
        **context.meta,
    )


def configure_commit(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-m", "--message", required=True, help="Commit message")
    parser.add_argument("-a", "--all", dest="commit_all", action="store_true", help="Stage tracked modifications first")


async def handle_commit(context: CommandContext) -> Any:
    return await context.client.commit(
        context.args.message,
        all=True if context.args.commit_all else None,
        **context.meta,
    )


def configure_pull(parser: argparse.ArgumentParser) -> None:
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--head", action="store_true", help="Pull repository heads")
    target.add_argument("--snapshot", help="Pull a workspace snapshot")


async def handle_pull(context: CommandContext) -> Any:
    if context.args.snapshot:
        return await context.client.pull_snapshot(context.args.snapshot, **context.meta)
    return await context.client.pull_head(**context.meta)


async def handle_push(context: CommandContext) -> Any:
    return await context.client.push(
        remote=context.meta.get("remote"),
        **_meta_without(context.meta, "remote"),
    )


def _meta_without(meta: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value for key, value in meta.items() if key not in keys}
