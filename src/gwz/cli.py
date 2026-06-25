from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import sys
from enum import Enum
from pathlib import Path
from typing import Any

from .client import Client
from .errors import GwzError


def _json_default(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gwz", description="Manage GWZ multi-repository workspaces")
    parser.add_argument("--root", help="Workspace root")
    parser.add_argument("--json", action="store_true", help="Render one JSON response")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Show workspace status")
    status.add_argument("--combined", action="store_true", help="Include combined workspace status")

    ls = sub.add_parser("ls", help="List workspace members")
    ls.add_argument("--materialized-only", action="store_true", help="Hide configured but missing members")

    init = sub.add_parser("init", help="Create or initialize a workspace")
    init.add_argument("urls", nargs="*", help="Initial source URLs")
    init.add_argument("--path", default="", help="Workspace root or source path prefix")

    materialize = sub.add_parser("materialize", help="Materialize workspace members")
    target = materialize.add_mutually_exclusive_group()
    target.add_argument("--lock", action="store_true", help="Materialize the workspace lock")
    target.add_argument("--head", action="store_true", help="Materialize repository heads")
    target.add_argument("--snapshot", help="Materialize a workspace snapshot")
    target.add_argument("--tag", help="Materialize a workspace tag")

    return parser


async def run(args: argparse.Namespace) -> int:
    async with Client(root=args.root) as client:
        if args.command == "status":
            response = await client.status(combined=args.combined)
        elif args.command == "ls":
            response = await client.ls(include_unmaterialized=not args.materialized_only)
        elif args.command == "init":
            if args.urls:
                response = await client.init_from_sources(args.urls, workspace_root=args.path)
            else:
                response = await client.create_workspace(args.path or args.root)
        elif args.command == "materialize":
            if args.head:
                response = await client.materialize("head")
            elif args.snapshot:
                response = await client.materialize("snapshot", name=args.snapshot)
            elif args.tag:
                response = await client.materialize("tag", name=args.tag)
            else:
                response = await client.materialize("lock")
        else:
            raise AssertionError(args.command)

    if args.json:
        print(json.dumps(response, default=_json_default, sort_keys=True))
    else:
        envelope = getattr(response, "response", None)
        meta = getattr(envelope, "meta", None)
        print(getattr(meta, "message", None) or getattr(meta, "aggregate_status", response))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(run(args))
    except GwzError as exc:
        print(f"gwz: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
