from __future__ import annotations

import argparse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .client import Client
from .client_helpers import SUCCESS_AGGREGATE_STATUS_NAMES
from .protocol.generated import SyncBehavior

CommandHandler = Callable[["CommandContext"], Awaitable[Any]]
ConfigureParser = Callable[[argparse.ArgumentParser], None]


GLOBAL_LIST_ATTRS = ("targets", "exclude_targets", "member_paths")
GLOBAL_BOOL_ATTRS = ("all_members", "dry_run", "partial", "destructive", "json")
GLOBAL_SCALAR_ATTRS = (
    "root",
    "sync",
    "remote",
    "jobs",
    "max_connections_per_host",
    "progress_min_interval_ms",
)
GLOBAL_DEST_PREFIXES = ("_cmd_", "_nested_")


class CliUsageError(ValueError):
    """Raised when parsed CLI options are semantically invalid."""


@dataclass(slots=True)
class CommandContext:
    args: argparse.Namespace
    client: Client
    meta: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    help: str
    handler: CommandHandler
    configure: ConfigureParser | None = None


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: list[CommandSpec] = []

    def register(
        self,
        name: str,
        *,
        help: str,
        handler: CommandHandler,
        configure: ConfigureParser | None = None,
    ) -> None:
        self._commands.append(
            CommandSpec(name=name, help=help, handler=handler, configure=configure)
        )

    def attach_to(
        self,
        subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
        *,
        global_parent_factory: Callable[[], argparse.ArgumentParser] | None = None,
    ) -> None:
        for command in self._commands:
            kwargs: dict[str, Any] = {}
            if global_parent_factory is not None:
                kwargs["parents"] = [global_parent_factory()]
                kwargs["conflict_handler"] = "resolve"
            parser = subparsers.add_parser(command.name, help=command.help, **kwargs)
            if command.configure is not None:
                command.configure(parser)
            parser.set_defaults(command_handler=command.handler)


class GwzArgumentParser(argparse.ArgumentParser):
    def parse_args(
        self,
        args: list[str] | None = None,
        namespace: argparse.Namespace | None = None,
    ) -> argparse.Namespace:
        parsed = super().parse_args(args, namespace)
        normalize_global_options(parsed)
        return parsed


def global_options_parent(dest_prefix: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False, argument_default=argparse.SUPPRESS)
    add_global_options(parser, dest_prefix=dest_prefix, defaults=False)
    return parser


def add_global_options(
    parser: argparse.ArgumentParser,
    *,
    dest_prefix: str = "",
    defaults: bool = True,
) -> None:
    def list_default() -> list[str] | str:
        return [] if defaults else argparse.SUPPRESS

    bool_default: bool | str = False if defaults else argparse.SUPPRESS
    scalar_default: Any = None if defaults else argparse.SUPPRESS

    parser.add_argument(
        "--root",
        dest=f"{dest_prefix}root",
        default=scalar_default,
        help="Workspace root",
    )
    parser.add_argument(
        "--target",
        "--member",
        dest=f"{dest_prefix}targets",
        action="append",
        default=list_default(),
        help="Select a workspace target selector",
    )
    parser.add_argument(
        "--no-target",
        "--no-member",
        dest=f"{dest_prefix}exclude_targets",
        action="append",
        default=list_default(),
        help="Exclude a workspace target selector",
    )
    parser.add_argument(
        "--member-path",
        dest=f"{dest_prefix}member_paths",
        action="append",
        default=list_default(),
        help="Select a workspace member path",
    )
    parser.add_argument(
        "--no-member-path",
        dest=f"{dest_prefix}exclude_targets",
        action="append",
        default=list_default(),
        help="Exclude a workspace member path",
    )
    parser.add_argument(
        "--all",
        dest=f"{dest_prefix}all_members",
        action="store_true",
        default=bool_default,
        help="Select all workspace targets",
    )
    parser.add_argument(
        "--dry-run",
        dest=f"{dest_prefix}dry_run",
        action="store_true",
        default=bool_default,
        help="Plan the operation without mutating state",
    )
    parser.add_argument(
        "--partial",
        dest=f"{dest_prefix}partial",
        action="store_true",
        default=bool_default,
        help="Allow operations to complete partially",
    )
    parser.add_argument(
        "--force",
        dest=f"{dest_prefix}destructive",
        action="store_true",
        default=bool_default,
        help="Allow destructive behavior when required",
    )
    parser.add_argument(
        "--sync",
        dest=f"{dest_prefix}sync",
        choices=("fetch-only", "ff-only", "merge", "rebase", "reset", "driver-selected"),
        default=scalar_default,
        help="Select workspace sync behavior",
    )
    parser.add_argument(
        "--remote",
        dest=f"{dest_prefix}remote",
        default=scalar_default,
        help="Select the git remote name",
    )
    parser.add_argument(
        "--jobs",
        dest=f"{dest_prefix}jobs",
        type=positive_int,
        default=scalar_default,
        help="Global ceiling on concurrent member operations",
    )
    parser.add_argument(
        "--max-per-host",
        dest=f"{dest_prefix}max_connections_per_host",
        type=positive_int,
        default=scalar_default,
        help="Max concurrent connections to any one host",
    )
    parser.add_argument(
        "--progress-interval",
        dest=f"{dest_prefix}progress_min_interval_ms",
        type=non_negative_int,
        default=scalar_default,
        help="Min milliseconds between progress events per repo",
    )
    parser.add_argument(
        "--json",
        dest=f"{dest_prefix}json",
        action="store_true",
        default=bool_default,
        help="Render one JSON response",
    )


def normalize_global_options(args: argparse.Namespace) -> None:
    for attr in GLOBAL_LIST_ATTRS:
        values = list(getattr(args, attr, []) or [])
        for prefix in GLOBAL_DEST_PREFIXES:
            values.extend(getattr(args, f"{prefix}{attr}", []) or [])
        setattr(args, attr, values)

    for attr in GLOBAL_BOOL_ATTRS:
        value = bool(getattr(args, attr, False))
        for prefix in GLOBAL_DEST_PREFIXES:
            value = value or bool(getattr(args, f"{prefix}{attr}", False))
        setattr(args, attr, value)

    for attr in GLOBAL_SCALAR_ATTRS:
        value = getattr(args, attr, None)
        for prefix in GLOBAL_DEST_PREFIXES:
            override = getattr(args, f"{prefix}{attr}", None)
            if override is not None:
                value = override
        setattr(args, attr, value)


def validate_args(args: argparse.Namespace) -> None:
    if (
        getattr(args, "command", None) == "repo"
        and getattr(args, "repo_command", None) == "sync"
        and getattr(args, "member_path", None)
        and (
            args.all_members
            or args.targets
            or args.exclude_targets
            or args.member_paths
        )
    ):
        raise CliUsageError("repo sync member path cannot be combined with global selection")


def meta_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if args.all_members:
        meta["all_members"] = True
    if args.targets:
        meta["targets"] = args.targets
    if args.exclude_targets:
        meta["exclude_targets"] = args.exclude_targets
    if args.member_paths:
        meta["paths"] = args.member_paths
    if args.dry_run:
        meta["dry_run"] = True
    if args.partial:
        meta["partial"] = True
    if args.destructive:
        meta["destructive"] = True
    if args.sync is not None:
        meta["sync"] = SyncBehavior[args.sync.replace("-", "_")]
    if args.remote is not None:
        meta["remote"] = args.remote
    if args.jobs is not None:
        meta["concurrency"] = args.jobs
    if args.max_connections_per_host is not None:
        meta["max_connections_per_host"] = args.max_connections_per_host
    if args.progress_min_interval_ms is not None:
        meta["progress_min_interval_ms"] = args.progress_min_interval_ms
    return meta


def exit_code_for_error(error: BaseException) -> int:
    return 2 if isinstance(error, CliUsageError) else 1


def exit_code_for_response(response: Any) -> int:
    envelope = getattr(response, "response", None)
    meta = getattr(envelope, "meta", None)
    aggregate = getattr(meta, "aggregate_status", None)
    if aggregate is None:
        return 0
    name = getattr(aggregate, "name", str(aggregate))
    return 0 if name in SUCCESS_AGGREGATE_STATUS_NAMES else 1


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed
