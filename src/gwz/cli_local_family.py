"""CLI surface for the local clone family (design §4-§7; LCM1.0c, lane CP).

One verb reaches the two new protocol slots, and the merge selector the third:

* ``gwz local clone <name> [dest]``     -> ``CloneLocalWorkspaceRequest``
* ``gwz local list|dispose|disband``    -> ``LocalFamilyRequest``
* ``gwz merge --remote <name> [<ref>]`` -> ``MergeRequest.local_source_name``

Creation lives under the family's own verb, with the member name positional
like ``dispose <name>``'s (operator ruling 2026-09-06, design §11 item 28).
``gwz clone --local`` was removed without an alias -- nothing had been released
-- so ``gwz clone`` is the URL form only and this module no longer touches it.
The wire did not move: the request ``local clone`` builds is the one
``clone --local`` built.

Requests are built field-for-field from the design's §7 CLI -> live message
table, and every flag shape the design refuses raises a typed
:class:`CliUsageError` before a request is encoded. Core owns every other
refusal: unknown hazard names, family lifecycle state, and the unsupported
family ``--dry-run``, which is therefore passed through rather than
pre-empted here. Those rows live in the one cross-driver parity fixture
``gwz-core/protocol/fixtures/cli_parity/local_family_cases.json`` (operator
ruling 1 of 2026-09-06, design §11 item 17); the Rust driver asserts against
the same file, and neither driver keeps a copy.

This module also renders ``gwz local list`` (design §8.1), because that is
the one response whose human form is a table of its own payload rather than
an envelope message. ``cli.run`` calls :func:`render_family_listing` for it,
which joins ``LocalFamilyResponse.root_path`` with each member's
root-relative ``path`` for the human table; ``--json`` takes the generic
protocol document unchanged, so a machine reader gets both wire fields as
core sent them.

``merge`` is registered by ``cli_merge``, which this lane does not own, and
argparse rejects a second subparser with that name. The command is therefore
extended in place: every merge without ``--remote`` still runs its original
handler, unchanged. ``clone`` (``cli_local``) is not extended at all.
"""

from __future__ import annotations

import argparse
import os.path
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
from .protocol.generated import (
    LocalCloneMode,
    LocalFamilyOp,
    LocalMemberState,
    LocalObservedState,
)

#: Design §5.2 hazard vocabulary. Core validates the names; the CLI only
#: refuses a force that names nothing at all.
HAZARD_NAMES = ("open-merge", "dirty", "unpreserved-history")

BARE_FORCE_REFUSAL = (
    "local dispose --force requires one or more hazard names ("
    + ", ".join(HAZARD_NAMES)
    + ")"
)

def register_commands(registry: CommandRegistry) -> None:
    registry.register(
        "local",
        help="Create, inspect and retire the local clone family",
        configure=configure_local,
        handler=handle_local,
    )
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
# gwz local clone <name> [dest]  (design §4; operator ruling 2026-09-06)
# ---------------------------------------------------------------------------


def configure_local_clone(parser: argparse.ArgumentParser) -> None:
    # The name is a required positional, as `dispose <name>` already has it;
    # a missing name is argparse's own error, an empty one the handler's.
    parser.add_argument("name", help="Family member name for the new clone")
    parser.add_argument(
        "dest",
        nargs="?",
        help="Destination directory (default ../<root-dirname>-<name>)",
    )
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


async def handle_local_clone(context: CommandContext) -> Any:
    args = context.args
    if not args.name:
        # Ruling 4 of 2026-09-06 (design §7, §11 item 20): an empty name
        # refuses here, before a workspace discovery and a family read learn
        # the same thing from core's own shape check.
        raise CliUsageError("local clone <name> must not be empty", code="InvalidRequest")
    mode = _local_clone_mode(args)
    if args.branch is not None and mode is LocalCloneMode.verbatim:
        raise CliUsageError(
            "-b <branch> is accepted only with --clean or --bare",
            code="InvalidRequest",
        )
    if args.from_source is not None and not args.from_source:
        # An absent `copy_source` is what "the cwd workspace" means (design
        # §4), so an empty one is not a shorter spelling of it: it would ask
        # core to resolve a nameless source.
        raise CliUsageError("--from <name|path> must not be empty", code="InvalidRequest")
    return await context.client.clone_local_workspace(
        args.name,
        dest=args.dest,
        mode=mode,
        branch=args.branch,
        # Design §7 tag 6, §11 item 11: `--from` is `copy_source` on the wire.
        # Whether the token names a family member or a path is core's to
        # resolve; the CLI carries it through unchanged.
        copy_source=args.from_source,
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


# ---------------------------------------------------------------------------
# gwz local list | dispose | disband  (design §5)
# ---------------------------------------------------------------------------


def configure_local(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="local_command", required=True)
    # One parent instance per nested parser: argparse's "resolve" handler
    # mutates the conflicting action, which parents share by reference, and
    # `dispose` redefines `--force` to take hazard names.
    configure_local_clone(
        subparsers.add_parser(
            "clone",
            help="Create a local clone of this workspace as a new family member",
            parents=[global_options_parent("_nested_")],
            conflict_handler="resolve",
        )
    )
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
    if command == "clone":
        return await handle_local_clone(context)
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
    # The split is on `,` and nothing else: no trimming, no case folding, no
    # normalization. A deletion waiver is the operator's authorization token,
    # and rewriting it before encoding would make the CLI answer for a
    # vocabulary it does not own. The Rust CLI splits identically (lane CR),
    # so the same argv encodes the same `force_hazards` in both drivers.
    names = raw.split(",")
    if not all(names):
        raise CliUsageError(BARE_FORCE_REFUSAL, code="InvalidRequest")
    # Unknown names are core's refusal (design §7), so they pass through.
    return names


# ---------------------------------------------------------------------------
# gwz local list rendering  (design §8.1 and the §7 members payload)
# ---------------------------------------------------------------------------

#: The observed state each recorded state projects to when the filesystem
#: agrees with the index (design §3.1). Anything else is a divergence an
#: operator has to see, so the row shows both states rather than one.
AGREEING_OBSERVATION = {
    LocalMemberState.creating: LocalObservedState.incomplete,
    LocalMemberState.ready: LocalObservedState.ready,
    LocalMemberState.disposing: LocalObservedState.interrupted_disposal,
}


def is_family_listing(response: Any) -> bool:
    """True for a `LocalFamilyResponse` that carries a list payload.

    `members` is populated for `op=list` and empty otherwise (design §7), so
    dispose, disband and an unpopulated list keep the envelope-message
    rendering they already had.
    """

    return (
        type(response).__name__ == "LocalFamilyResponse"
        and bool(getattr(response, "members", None))
    )


def render_family_listing(response: Any) -> str:
    """The design §8.1 table: name, kind, state, path -- one row per member.

    `local list` is observation-only (design §3.1): it reports, and never
    repairs. So the state column shows what was *observed*, which is what an
    operator can act on, and falls back to `<recorded>/<observed>` whenever
    the two disagree -- neither state may be silently dropped in favour of
    the other. A recorded `last_error` follows its row on its own line, where
    free text cannot break the column alignment.

    The path column is :func:`member_display_path`: the absolute paths in the
    §8.1 sample are the response's own two halves joined, never a guess.
    """

    root_path = getattr(response, "root_path", None)
    rows = [
        (
            entry.name,
            _enum_name(entry.kind),
            _state_cell(entry),
            member_display_path(root_path, entry.path),
            entry.last_error,
        )
        for entry in response.members
    ]
    if not rows:
        return ""
    widths = [max(len(row[column]) for row in rows) + 2 for column in range(3)]
    lines: list[str] = []
    for name, kind, state, path, last_error in rows:
        lines.append(
            f"{name.ljust(widths[0])}{kind.ljust(widths[1])}"
            f"{state.ljust(widths[2])}{path}"
        )
        if last_error:
            lines.append(f"  last error: {last_error}")
    return "\n".join(lines)


def member_display_path(root_path: str | None, path: str) -> str:
    """One member's path as a person reads it (design §8.1, §11 item 19).

    A member's `path` is root-relative on the wire -- `.` for the root, a
    normalised root-escaping path such as `../gwz-dev-A` for a clone -- and
    `LocalFamilyResponse.root_path` is the root as core observed it, reached
    through this workspace's pointer when the listing runs in a clone. The
    absolute paths of the §8.1 sample listing are those two joined, which is
    why they are literal: both halves came off the wire.

    The join is lexical. It resolves the leading `..` run against the root's
    own directory names and nothing else: no filesystem is touched, no symlink
    is followed, no path is canonicalized. Deciding whether two spellings name
    one directory belongs to core, which has the filesystem (design §3.1).

    An absent `root_path` -- an older core, or one that answered `list` without
    a family -- renders the relative path unchanged. Naming a root the response
    did not carry would be exactly the guess the design forbids.
    """

    if not root_path:
        return path
    # The wire spells a member path with `/` whatever the host is
    # (`gwz_family_model::normalize`); `normpath` maps that to the platform's
    # own separator, which is how `root_path` is already spelled. The path is
    # joined whole, never split on `/` first: splitting dropped the leading
    # empty segment of an absolute member path and nested it under the root,
    # where the Rust driver's join lets it replace the root -- the one case
    # the cross-driver listing fixture found the two joins disagreeing on
    # (LCM1.1 fix 3, lane C, 2026-09-06). The wire never carries an absolute
    # member path; if one ever arrives, both drivers now show it as recorded.
    return os.path.normpath(os.path.join(root_path, path))


def _state_cell(entry: Any) -> str:
    recorded, observed = entry.recorded_state, entry.observed_state
    if AGREEING_OBSERVATION.get(recorded) is observed:
        return _enum_name(observed)
    return f"{_enum_name(recorded)}/{_enum_name(observed)}"


def _enum_name(value: Any) -> str:
    return getattr(value, "name", str(value))


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
