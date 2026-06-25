from __future__ import annotations

from gwz.cli import build_parser


def test_cli_parser_accepts_status() -> None:
    args = build_parser().parse_args(["--root", ".", "--json", "status", "--combined"])
    assert args.command == "status"
    assert args.root == "."
    assert args.json is True
    assert args.combined is True
