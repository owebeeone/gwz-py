from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from gwz.cli import build_parser

from test_merge_cli_cross_driver import (
    conflict_workspace,
    copy_pair,
    fast_forward_workspace,
    normalize,
    resolve_conflict,
    run_cli,
    rust_gwz_binary,
)


def run_cli_mode(
    driver: str,
    rust_binary: Path,
    root: Path,
    output_mode: str,
    command: list[str],
) -> tuple[int, str]:
    executable = (
        [str(rust_binary)]
        if driver == "rust"
        else [sys.executable, "-m", "gwz.cli"]
    )
    mode = [] if output_mode == "human" else [f"--{output_mode}"]
    result = subprocess.run(
        [*executable, "--root", str(root), *mode, *command],
        cwd=root.parent,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
    )
    assert result.stderr == "", (driver, result.stderr)
    return result.returncode, result.stdout


def normalize_human(output: str, root: Path) -> str:
    output = output.replace(str(root), "<root>")
    output = re.sub(
        r"(?<![0-9a-f])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
        r"[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f])",
        "<uuid>",
        output,
        flags=re.IGNORECASE,
    )
    return re.sub(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", "<oid>", output)


def exact_commit_message(repo: Path, commit: str) -> bytes:
    raw = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "commit", commit],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout
    return raw.split(b"\n\n", 1)[1]


def test_merge_help_exposes_custom_messages_and_the_activated_no_ff_surface(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A1 unhid ``--no-ff``.

    The flag carried ``help=argparse.SUPPRESS`` while the v1 record lifecycle
    was a compile-gated boundary; the activation made it public, at parity
    with the Rust CLI's help text.
    """
    with pytest.raises(SystemExit) as stopped:
        build_parser().parse_args(["merge", "--help"])
    assert stopped.value.code == 0
    help_text = capsys.readouterr().out
    assert "--ff-only" in help_text
    assert "--no-ff" in help_text
    assert "Always create a merge commit" in help_text
    assert "--message" in help_text
    assert "custom merge commit-message body" in help_text


@pytest.mark.parametrize("output_mode", ["human", "json", "jsonl"])
def test_custom_message_start_is_equivalent_in_every_driver_output_mode(
    output_mode: str,
    tmp_path: Path,
    rust_gwz_binary: Path,
) -> None:
    template = tmp_path / "template"
    fast_forward_workspace(template)
    rust_root, python_root = copy_pair(template, tmp_path)
    command = [
        "--dry-run",
        "merge",
        "feature/source",
        "-m",
        "Parity body\r\nsecond line\r",
    ]
    rust_code, rust_output = run_cli_mode(
        "rust", rust_gwz_binary, rust_root, output_mode, command
    )
    python_code, python_output = run_cli_mode(
        "python", rust_gwz_binary, python_root, output_mode, command
    )
    assert rust_code == python_code == 0
    if output_mode == "human":
        assert normalize_human(rust_output, rust_root) == normalize_human(
            python_output, python_root
        )
    else:
        rust_records = [json.loads(line) for line in rust_output.splitlines()]
        python_records = [json.loads(line) for line in python_output.splitlines()]
        assert normalize(rust_records, rust_root) == normalize(
            python_records, python_root
        )


def test_custom_message_bytes_and_recovery_are_equivalent_across_drivers(
    tmp_path: Path,
    rust_gwz_binary: Path,
) -> None:
    template = tmp_path / "template"
    conflict_workspace(template)
    rust_root, python_root = copy_pair(template, tmp_path)
    custom_body = "Coordinated change\r\n\r\nPreserve this body\r\n"

    starts: list[tuple[Path, list[dict[str, Any]]]] = []
    for driver, root in [("rust", rust_root), ("python", python_root)]:
        code, records = run_cli(
            driver,
            rust_gwz_binary,
            root,
            ["merge", "feature/source", "-m", custom_body],
        )
        assert code == 1
        starts.append((root, records))
    assert normalize(starts[0][1], rust_root) == normalize(starts[1][1], python_root)

    for root in [rust_root, python_root]:
        resolve_conflict(root)
    continuations: list[tuple[Path, list[dict[str, Any]]]] = []
    for driver, root in [("rust", rust_root), ("python", python_root)]:
        code, records = run_cli(driver, rust_gwz_binary, root, ["merge", "--continue"])
        assert code == 0
        continuations.append((root, records))
    assert normalize(continuations[0][1], rust_root) == normalize(
        continuations[1][1], python_root
    )

    for (root, start_records), (_, continued_records) in zip(
        starts, continuations, strict=True
    ):
        start_terminal = start_records[-1]
        continued_terminal = continued_records[-1]
        merge_id = start_terminal["merge"]["merge_id"]
        operation_id = start_terminal["meta"]["operation_id"]
        result = continued_terminal["merge"]["repos"][0]["resulting_commit"]
        expected = (
            "Coordinated change\n\nPreserve this body\n\n"
            f"GWZ-Merge-ID: {merge_id}\nGWZ-Operation-ID: {operation_id}"
        ).encode()
        assert exact_commit_message(root / "repos" / "app", result) == expected
