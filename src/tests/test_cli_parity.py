from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from gwz.cli import build_parser
from gwz.cli_shared import CliUsageError, CommandContext, meta_kwargs, validate_args


FIXTURE = Path(__file__).parent / "fixtures" / "cli_parity" / "parser_cases.json"


class FakeClient:
    root = None

    async def ls(self, **kwargs: Any) -> Any:
        raise AssertionError("semantic parser tests should fail before client calls")


def cases() -> dict[str, list[list[str]]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("argv", cases()["accept"])
def test_cli_parity_accepts_representative_command_shapes(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)

    validate_args(args)
    assert callable(args.command_handler)


@pytest.mark.parametrize("argv", cases()["parse_reject"])
def test_cli_parity_rejects_invalid_command_shapes_at_parse(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(argv)

    assert exc_info.value.code != 0


@pytest.mark.parametrize("argv", cases()["semantic_reject"])
def test_cli_parity_rejects_invalid_command_shapes_semantically(argv: list[str]) -> None:
    args = build_parser().parse_args(argv)

    with pytest.raises(CliUsageError):
        validate_args(args)
        context = CommandContext(args=args, client=FakeClient(), meta=meta_kwargs(args))
        asyncio.run(args.command_handler(context))
