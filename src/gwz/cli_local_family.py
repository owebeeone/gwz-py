"""CLI surface for the local clone family (design §4-§7; LCM1.0c, lane CP).

Three verbs reach the two new protocol slots and the merge selector:

* ``gwz clone --local --name <name> [dest]`` -> ``CloneLocalWorkspaceRequest``
* ``gwz local list|dispose|disband``         -> ``LocalFamilyRequest``
* ``gwz merge --remote <name> [<ref>]``      -> ``MergeRequest.local_source_name``

Requests are built field-for-field from the design's §7 CLI -> live message
table, and every flag shape the design refuses raises a typed
:class:`CliUsageError` before a request is encoded. Core owns every other
refusal: unknown hazard names, family lifecycle state, and the unsupported
family ``--dry-run``, which is therefore passed through rather than
pre-empted here.

``clone`` and ``merge`` are registered by ``cli_local`` and ``cli_merge``,
which this lane does not own, and argparse rejects a second subparser with
either name. The two commands are therefore extended in place: the URL clone
and every merge without ``--remote`` still run their original handler,
unchanged.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from .cli_shared import (
    CliUsageError,
    CommandContext,
    CommandHandler,
    CommandRegistry,
    ConfigureParser,
    global_options_parent,
)
from .protocol.generated import LocalCloneMode, LocalFamilyOp

#: Design §5.2 hazard vocabulary. Core validates the names; the CLI only
#: refuses a force that names nothing at all.
HAZARD_NAMES = ("open-merge", "dirty", "unpreserved-history")

BARE_FORCE_REFUSAL = (
    "local dispose --force requires one or more hazard names ("
    + ", ".join(HAZARD_NAMES)
    + ")"
)

#: `clone` flags that only mean something with `--local`.
LOCAL_ONLY_FLAGS = (
    ("name", "--name"),
    ("verbatim", "--verbatim"),
    ("clean", "--clean"),
    ("bare", "--bare"),
    ("branch", "-b"),
    ("from_source", "--from"),
)


def register_commands(registry: CommandRegistry) -> None:
    registry.register(
        "local",
        help="Inspect and manage the local clone family",
        configure=configure_local,
        handler=handle_local,
    )
    extend_command(registry, "clone", configure=configure_clone_local, wrap=wrap_clone)
    extend_command(registry, "merge", configure=configure_merge_remote, wrap=wrap_merge)


def extend_command(
    registry: CommandRegistry,
    name: str,
    *,
    configure: ConfigureParser | None = None,
    wrap: Callable[[CommandHandler], CommandHandler] | None = None,
) -> None:
    """Layer the family surface onto a command another module registered."""

    commands = registry._commands  # noqa: SLF001 - see the module docstring
    for index, spec in enumerate(commands):
        if spec.name != name:
            continue
        commands[index] = replace(
            spec,
            configure=_chain_configure(spec.configure, configure),
            handler=spec.handler if wrap is None else wrap(spec.handler),
        )
        return
    raise AssertionError(f"local family: no registered '{name}' command to extend")


def _chain_configure(
    base: ConfigureParser | None, extra: ConfigureParser | None
) -> ConfigureParser | None:
    if extra is None:
        return base

    def configure(parser: argparse.ArgumentParser) -> None:
        if base is not None:
            base(parser)
        extra(parser)

    return configure


# ---------------------------------------------------------------------------
# gwz clone --local  (design §4)
# ---------------------------------------------------------------------------


def configure_clone_local(parser: argparse.ArgumentParser) -> None:
    # A local create has no URL (design §4), so the URL clone's positional
    # becomes optional and carries the destination in `--local` mode.
    _make_positional_optional(parser, "url")
    parser.add_argument(
        "--local",
        action="store_true",
        help="Create a local clone of this workspace instead of cloning a URL",
    )
    parser.add_argument("--name", metavar="name", help="Family name for the new clone")
    parser.add_argument(
        "--verbatim",
        action="store_true",
        help="Copy the source tree as it sits, dirt included (default)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Check out the frozen commit without the source's worktree dirt",
    )
    parser.add_argument(
        "--bare",
        action="store_true",
        help="Create a bare hub workspace (implies --clean)",
    )
    parser.add_argument(
        "-b",
        dest="branch",
        metavar="branch",
        help="Create this branch in every member (--clean or --bare only)",
    )
    parser.add_argument(
        "--from",
        dest="from_source",
        metavar="name|path",
        help="Copy source: a family name or a path (default: this workspace)",
    )


def wrap_clone(base: CommandHandler) -> CommandHandler:
    async def handle_clone(context: CommandContext) -> Any:
        if getattr(context.args, "local", False):
            return await handle_clone_local(context)
        _reject_local_only_flags(context.args)
        if not context.args.url:
            raise CliUsageError(
                "clone requires a workspace URL, or --local --name <name>",
                code="InvalidRequest",
            )
        return await base(context)

    return handle_clone


async def handle_clone_local(context: CommandContext) -> Any:
    args = context.args
    if args.directory is not None:
        raise CliUsageError(
            "clone --local takes one destination path", code="InvalidRequest"
        )
    if not args.name:
        raise CliUsageError(
            "clone --local requires --name <name>", code="InvalidRequest"
        )
    mode = _local_clone_mode(args)
    if args.branch is not None and mode is LocalCloneMode.verbatim:
        raise CliUsageError(
            "clone --local -b <branch> requires --clean or --bare",
            code="InvalidRequest",
        )
    if args.from_source is not None:
        # Design §7 holds `CloneLocalWorkspaceRequest` tag 6 unallocated
        # pending an operator decision on its wire name, so there is no field
        # to encode `--from` into yet.
        raise CliUsageError(
            "clone --local --from is not yet supported", code="InvalidRequest"
        )
    return await context.client.clone_local_workspace(
        args.name,
        dest=args.url,
        mode=mode,
        branch=args.branch,
        **context.meta,
    )


def _local_clone_mode(args: argparse.Namespace) -> LocalCloneMode:
    if args.verbatim and args.clean:
        raise CliUsageError(
            "--verbatim and --clean are mutually exclusive", code="InvalidRequest"
        )
    if args.verbatim and args.bare:
        raise CliUsageError(
            "--verbatim and --bare are mutually exclusive (--bare implies --clean)",
            code="InvalidRequest",
        )
    if args.bare:
        return LocalCloneMode.bare
    if args.clean:
        return LocalCloneMode.clean
    return LocalCloneMode.verbatim


def _reject_local_only_flags(args: argparse.Namespace) -> None:
    for attribute, flag in LOCAL_ONLY_FLAGS:
        value = getattr(args, attribute, None)
        if value:
            raise CliUsageError(
                f"clone {flag} requires --local", code="InvalidRequest"
            )


def _make_positional_optional(parser: argparse.ArgumentParser, dest: str) -> None:
    for action in parser._actions:  # noqa: SLF001 - argparse has no public form
        if action.dest == dest and not action.option_strings:
            action.nargs = "?"
            return
    raise AssertionError(f"clone parser has no positional '{dest}'")


# ---------------------------------------------------------------------------
# gwz local list | dispose | disband  (design §5)
# ---------------------------------------------------------------------------


def configure_local(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="local_command", required=True)
    # One parent instance per nested parser: argparse's "resolve" handler
    # mutates the conflicting action, which parents share by reference, and
    # `dispose` redefines `--force` to take hazard names.
    subparsers.add_parser(
        "list",
        help="List the family this workspace belongs to",
        parents=[global_options_parent("_nested_")],
        conflict_handler="resolve",
    )
    dispose = subparsers.add_parser(
        "dispose",
        help="Remove one family member",
        parents=[global_options_parent("_nested_")],
        conflict_handler="resolve",
    )
    dispose.add_argument("name", help="Family member name")
    dispose.add_argument(
        "--keep",
        action="store_true",
        help="Remove only the pointer and row; leave every file on disk",
    )
    dispose.add_argument(
        "--force",
        dest="force",
        nargs="?",
        const="",
        metavar="hazard[,hazard...]",
        help="Authorize named hazards: " + ", ".join(HAZARD_NAMES),
    )
    subparsers.add_parser(
        "disband",
        help="Remove the family pointers and index; leave every tree on disk",
        parents=[global_options_parent("_nested_")],
        conflict_handler="resolve",
    )


async def handle_local(context: CommandContext) -> Any:
    command = context.args.local_command
    if command == "list":
        return await context.client.local_family(LocalFamilyOp.list, **context.meta)
    if command == "disband":
        return await context.client.local_family(LocalFamilyOp.disband, **context.meta)
    if command == "dispose":
        keep = bool(context.args.keep)
        hazards = _force_hazards(context.args)
        if keep and hazards:
            raise CliUsageError(
                "local dispose --keep cannot be combined with --force",
                code="InvalidRequest",
            )
        return await context.client.local_family(
            LocalFamilyOp.dispose,
            name=context.args.name,
            keep=True if keep else None,
            force_hazards=hazards,
            **context.meta,
        )
    raise AssertionError(command)


def _force_hazards(args: argparse.Namespace) -> list[str]:
    raw = getattr(args, "force", None)
    if raw is None:
        # A bare global `--force` before the subcommand is the same refusal:
        # dispose authorizes named hazards, never everything (design §5.2).
        if getattr(args, "destructive", False):
            raise CliUsageError(BARE_FORCE_REFUSAL, code="InvalidRequest")
        return []
    names = raw.split(",")
    if not all(names):
        raise CliUsageError(BARE_FORCE_REFUSAL, code="InvalidRequest")
    # Unknown names are core's refusal (design §7), so they pass through.
    return names


# ---------------------------------------------------------------------------
# gwz merge --remote <name> [<ref>]  (design §6, §7)
# ---------------------------------------------------------------------------


def configure_merge_remote(parser: argparse.ArgumentParser) -> None:
    # `--remote` is a global option whose help ("git remote name") is wrong for
    # merge, where the token is family-only (design §6). Redefining it on this
    # subparser keeps the global dest and suppressed default -- so option
    # normalization is unchanged -- and says what the token means here.
    parser.add_argument(
        "--remote",
        dest="_cmd_remote",
        default=argparse.SUPPRESS,
        metavar="name",
        help="Merge from this local clone family member (not a git remote)",
    )


def wrap_merge(base: CommandHandler) -> CommandHandler:
    async def handle_merge(context: CommandContext) -> Any:
        name = getattr(context.args, "remote", None)
        if name is None:
            return await base(context)
        _reject_non_start_family_merge(context.args)
        # For merge, `--remote` is family-only: it is a `MergeRequest` field,
        # not `OperationPolicy.remote` (design §6). `push` moves the same token
        # out of the policy meta the same way. Presentation, streaming and the
        # rest of the merge request stay with `cli_merge`.
        meta = dict(context.meta)
        meta.pop("remote", None)
        meta["local_source_name"] = name
        return await base(replace(context, meta=meta))

    return handle_merge


def _reject_non_start_family_merge(args: argparse.Namespace) -> None:
    if (
        args.resume
        or args.abort
        or args.status is not None
        or args.gc is not None
    ):
        raise CliUsageError(
            "merge --remote <name> is accepted only when starting a merge",
            code="InvalidRequest",
        )
