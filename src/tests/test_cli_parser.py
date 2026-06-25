from __future__ import annotations

import argparse

import pytest

from gwz.cli import build_parser
from gwz.cli_shared import (
    CliUsageError,
    CommandContext,
    CommandRegistry,
    meta_kwargs,
    validate_args,
)
from gwz.protocol.generated import SyncBehavior


@pytest.mark.parametrize(
    "argv,command",
    [
        (["status", "--combined"], "status"),
        (["ls", "--materialized-only"], "ls"),
        (["init", "https://example.invalid/repo.git"], "init"),
        (["materialize", "--snapshot", "snap_1"], "materialize"),
    ],
)
def test_current_commands_register_handlers(argv: list[str], command: str) -> None:
    args = build_parser().parse_args(argv)

    assert args.command == command
    assert callable(args.command_handler)


def test_global_options_build_client_meta_kwargs() -> None:
    args = build_parser().parse_args(
        [
            "--root",
            "/ws",
            "--member",
            "mem_app",
            "--member-path",
            "repos/lib",
            "--dry-run",
            "--partial",
            "--force",
            "--sync",
            "reset",
            "--remote",
            "origin",
            "--jobs",
            "4",
            "--max-per-host",
            "2",
            "--progress-interval",
            "0",
            "--json",
            "status",
            "--combined",
        ]
    )

    validate_args(args)

    assert args.root == "/ws"
    assert args.json is True
    assert meta_kwargs(args) == {
        "member_ids": ["mem_app"],
        "paths": ["repos/lib"],
        "dry_run": True,
        "partial": True,
        "destructive": True,
        "sync": SyncBehavior.reset,
        "remote": "origin",
        "concurrency": 4,
        "max_connections_per_host": 2,
        "progress_min_interval_ms": 0,
    }


def test_all_rejects_specific_member_selection() -> None:
    args = build_parser().parse_args(["--all", "--member", "mem_app", "status"])

    with pytest.raises(CliUsageError, match="--all cannot be combined"):
        validate_args(args)


def test_command_registry_allows_modules_to_attach_commands() -> None:
    async def handler(context: CommandContext) -> str:
        return str(context.args.flag)

    def configure(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--flag", action="store_true")

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    registry = CommandRegistry()
    registry.register("demo", help="Demo command", configure=configure, handler=handler)
    registry.attach_to(subparsers)

    args = parser.parse_args(["demo", "--flag"])

    assert args.command == "demo"
    assert args.flag is True
    assert args.command_handler is handler
