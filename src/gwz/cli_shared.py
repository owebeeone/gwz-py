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

    def attach_to(self, subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
        for command in self._commands:
            parser = subparsers.add_parser(command.name, help=command.help)
            if command.configure is not None:
                command.configure(parser)
            parser.set_defaults(command_handler=command.handler)


def add_global_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", help="Workspace root")
    parser.add_argument(
        "--member",
        dest="member_ids",
        action="append",
        default=[],
        help="Select a workspace member by id",
    )
    parser.add_argument(
        "--member-path",
        dest="member_paths",
        action="append",
        default=[],
        help="Select a workspace member by path",
    )
    parser.add_argument(
        "--all",
        dest="all_members",
        action="store_true",
        help="Select all workspace members",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan the operation without mutating state",
    )
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Allow operations to complete partially",
    )
    parser.add_argument(
        "--force",
        dest="destructive",
        action="store_true",
        help="Allow destructive behavior when required",
    )
    parser.add_argument(
        "--sync",
        choices=("fetch-only", "ff-only", "merge", "rebase", "reset", "driver-selected"),
        help="Select workspace sync behavior",
    )
    parser.add_argument("--remote", help="Select the git remote name")
    parser.add_argument(
        "--jobs",
        type=positive_int,
        help="Global ceiling on concurrent member operations",
    )
    parser.add_argument(
        "--max-per-host",
        dest="max_connections_per_host",
        type=positive_int,
        help="Max concurrent connections to any one host",
    )
    parser.add_argument(
        "--progress-interval",
        dest="progress_min_interval_ms",
        type=non_negative_int,
        help="Min milliseconds between progress events per repo",
    )
    parser.add_argument("--json", action="store_true", help="Render one JSON response")


def validate_args(args: argparse.Namespace) -> None:
    if args.all_members and (args.member_ids or args.member_paths):
        raise CliUsageError("--all cannot be combined with --member or --member-path")


def meta_kwargs(args: argparse.Namespace) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if args.all_members:
        meta["all_members"] = True
    if args.member_ids:
        meta["member_ids"] = args.member_ids
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
