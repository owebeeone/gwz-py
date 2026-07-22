from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from native_helpers import (
    commit_file,
    create_workspace_with_member,
    git,
    native_client,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def rust_gwz_binary() -> Path:
    subprocess.run(
        ["cargo", "build", "-p", "gwz", "--bin", "gwz"],
        cwd=REPO_ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    binary = REPO_ROOT / "target" / "debug" / "gwz"
    assert binary.is_file()
    return binary


def fast_forward_workspace(root: Path) -> None:
    repo, _ = create_workspace_with_member(root)
    git(repo, "checkout", "-b", "feature/source")
    commit_file(repo, "source.txt", "source\n", "source")
    git(repo, "checkout", "main")


def conflict_workspace(root: Path) -> None:
    repo, _ = create_workspace_with_member(root)
    git(repo, "checkout", "-b", "feature/source")
    commit_file(repo, "README.md", "source\n", "source")
    git(repo, "checkout", "main")
    commit_file(repo, "README.md", "target\n", "target")


def preflight_failure_workspace(root: Path) -> None:
    client = native_client(root)
    asyncio.run(client.create_workspace(workspace_id="ws_cli_parity"))
    asyncio.run(client.create_repo("repos/app", member_id="mem_app", source_id="src_app"))
    asyncio.run(client.create_repo("repos/lib", member_id="mem_lib", source_id="src_lib"))
    app = root / "repos" / "app"
    lib = root / "repos" / "lib"
    commit_file(app, "README.md", "app\n", "initial app")
    commit_file(lib, "README.md", "lib\n", "initial lib")
    git(app, "branch", "feature/source")
    asyncio.run(client.capture(paths=["repos/app", "repos/lib"]))


def copy_pair(template: Path, root: Path) -> tuple[Path, Path]:
    rust_root = root / "rust"
    python_root = root / "python"
    shutil.copytree(template, rust_root)
    shutil.copytree(template, python_root)
    return rust_root, python_root


def run_cli(
    driver: str,
    rust_binary: Path,
    root: Path,
    command: list[str],
) -> tuple[int, list[dict[str, Any]]]:
    executable = (
        [str(rust_binary)]
        if driver == "rust"
        else [sys.executable, "-m", "gwz.cli"]
    )
    result = subprocess.run(
        [*executable, "--root", str(root), "--jsonl", *command],
        cwd=root,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=20,
    )
    assert result.stderr == "", (driver, result.stderr)
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert records, driver
    assert all(isinstance(record, dict) for record in records)
    return result.returncode, records


def dynamic_values(records: list[dict[str, Any]]) -> dict[str, set[str]]:
    values = {
        "operation_id": set(),
        "request_id": set(),
        "merge_id": set(),
    }

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in values and isinstance(item, str):
                    values[key].add(item)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(records)
    return values


def normalize(records: list[dict[str, Any]], root: Path) -> list[dict[str, Any]]:
    values = dynamic_values(records)
    replacements = sorted(
        (
            (value, f"<{kind}>")
            for kind, items in values.items()
            for value in items
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    def visit(value: Any, key: str | None = None) -> Any:
        if key in {"operation_id", "request_id", "merge_id"} and value is not None:
            return f"<{key}>"
        if key in {"timestamp_ms", "started_at_ms", "finished_at_ms"}:
            return "<timestamp>"
        if isinstance(value, dict):
            return {item_key: visit(item, item_key) for item_key, item in value.items()}
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, str):
            return value

        normalized = value.replace(str(root), "<root>")
        for dynamic, replacement in replacements:
            normalized = normalized.replace(dynamic, replacement)
        normalized = re.sub(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", "<oid>", normalized)
        return normalized

    return visit(records)


def assert_parity(
    rust_binary: Path,
    rust_root: Path,
    python_root: Path,
    command: list[str],
) -> None:
    rust_code, rust_records = run_cli("rust", rust_binary, rust_root, command)
    python_code, python_records = run_cli("python", rust_binary, python_root, command)
    assert rust_code == python_code
    assert normalize(rust_records, rust_root) == normalize(python_records, python_root)


def start_conflict_pair(
    rust_binary: Path,
    rust_root: Path,
    python_root: Path,
) -> None:
    assert_parity(
        rust_binary,
        rust_root,
        python_root,
        ["merge", "feature/source"],
    )


def resolve_conflict(root: Path) -> None:
    repo = root / "repos" / "app"
    (repo / "README.md").write_text("resolved\n", encoding="utf-8")
    git(repo, "add", "README.md")


def create_manual_merge_commit(root: Path) -> None:
    resolve_conflict(root)
    git(root / "repos" / "app", "commit", "-m", "manual merge")


ScenarioSetup = Callable[[Path], None]


def setup_for_scenario(scenario: str) -> ScenarioSetup:
    if scenario == "preflight_failure":
        return preflight_failure_workspace
    if scenario in {
        "expected_conflict",
        "status",
        "continue",
        "recovery_rejection",
        "abort",
    }:
        return conflict_workspace
    return fast_forward_workspace


@pytest.mark.parametrize(
    "scenario",
    [
        "dry_run_start",
        "clean_start",
        "expected_conflict",
        "status",
        "continue",
        "recovery_rejection",
        "abort",
        "preflight_failure",
    ],
)
def test_actual_rust_and_python_merge_jsonl_are_semantically_equivalent(
    scenario: str,
    tmp_path: Path,
    rust_gwz_binary: Path,
) -> None:
    template = tmp_path / "template"
    setup_for_scenario(scenario)(template)
    rust_root, python_root = copy_pair(template, tmp_path)

    if scenario == "dry_run_start":
        assert_parity(
            rust_gwz_binary,
            rust_root,
            python_root,
            ["--dry-run", "merge", "feature/source"],
        )
    elif scenario in {"clean_start", "expected_conflict", "preflight_failure"}:
        assert_parity(
            rust_gwz_binary,
            rust_root,
            python_root,
            ["merge", "feature/source"],
        )
    else:
        start_conflict_pair(rust_gwz_binary, rust_root, python_root)
        if scenario == "status":
            command = ["merge", "--status"]
        elif scenario == "continue":
            resolve_conflict(rust_root)
            resolve_conflict(python_root)
            command = ["merge", "--continue"]
        elif scenario == "recovery_rejection":
            create_manual_merge_commit(rust_root)
            create_manual_merge_commit(python_root)
            command = ["merge", "--continue"]
        else:
            command = ["merge", "--abort"]
        assert_parity(rust_gwz_binary, rust_root, python_root, command)
