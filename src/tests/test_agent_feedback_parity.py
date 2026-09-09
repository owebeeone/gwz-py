"""Small native regression for the agent-trial registration/status/sync cycle."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from gwz.errors import GwzOperationError
from gwz.protocol.generated import AggregateStatus, LockDifferenceReason, LockMatch
from native_helpers import create_git_repo, git, native_client


def _drivers() -> tuple[list[str], list[str]]:
    rust_bin = os.environ.get("GWZ_RUST_BIN")
    if not rust_bin:
        pytest.skip("run_tests.py provides the matching Rust CLI")
    return [rust_bin], [sys.executable, "-m", "gwz.cli"]


def _run_driver(driver: list[str], cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*driver, *args], cwd=cwd, check=False, capture_output=True, text=True
    )


def _listed_entries(document: dict[str, object]) -> list[dict[str, object]]:
    # The Rust CLI's JSON projection calls these `entries`; Python's decoded
    # response retains the protocol field name `members`.
    entries = document.get("entries")
    if entries is None:
        entries = document["members"]
    return entries  # type: ignore[return-value]


@pytest.mark.parametrize(
    ("root_args", "caller_kind"),
    [
        ((), "root"),
        (("--root", "."), "root"),
        (("--root", "Harbor Workspace"), "parent"),
        (("--root", "absolute"), "outside"),
    ],
)
def test_listing_root_forms_match_cli_and_python(
    tmp_path: Path, root_args: tuple[str, ...], caller_kind: str
) -> None:
    workspace = tmp_path / "Harbor Workspace"
    native = native_client(workspace)
    asyncio.run(native.create_workspace(workspace_id="ws_root_forms"))
    asyncio.run(native.create_repo("members/app", member_id="mem_app", source_id="src_app"))
    outside = tmp_path / "executor-b"
    outside.mkdir()
    caller = {"root": workspace, "parent": tmp_path, "outside": outside}[caller_kind]
    args = list(root_args)
    if args == ["--root", "absolute"]:
        args[1] = str(workspace)

    for driver in _drivers():
        result = _run_driver(driver, caller, *args, "--json", "ls")
        assert result.returncode == 0, result.stderr
        document = json.loads(result.stdout)
        entries = _listed_entries(document)
        assert str(workspace / "members/app") in {entry["abspath"] for entry in entries}
        assert all(Path(entry["abspath"]).is_absolute() for entry in entries)


def test_member_and_outside_callers_preserve_driver_path_meaning(tmp_path: Path) -> None:
    workspace = tmp_path / "Harbor Workspace"
    native = native_client(workspace)
    asyncio.run(native.create_workspace(workspace_id="ws_caller_locations"))
    asyncio.run(native.create_repo("members/app", member_id="mem_app", source_id="src_app"))
    member = workspace / "members/app"
    outside = tmp_path / "executor-b"
    outside.mkdir()

    for driver in _drivers():
        member_result = _run_driver(driver, member, "--json", "ls")
        outside_result = _run_driver(
            driver, outside, "--root", str(workspace), "--json", "ls"
        )
        assert member_result.returncode == outside_result.returncode == 0
        assert _listed_entries(json.loads(member_result.stdout)) == _listed_entries(
            json.loads(outside_result.stdout)
        )


def test_rejected_add_reports_same_resolved_path_facts_for_both_drivers(tmp_path: Path) -> None:
    workspace = tmp_path / "Harbor Workspace"
    native = native_client(workspace)
    asyncio.run(native.create_workspace(workspace_id="ws_diagnostic_facts"))
    supplied = "missing/repository"

    for driver in _drivers():
        result = _run_driver(
            driver, tmp_path, "--root", str(workspace), "repo", "add", supplied
        )
        assert result.returncode != 0
        assert supplied in result.stderr
        assert str((tmp_path / supplied).resolve()) in result.stderr
        assert str(tmp_path) in result.stderr
        for mode in ("--json", "--jsonl"):
            machine = _run_driver(
                driver,
                tmp_path,
                "--root",
                str(workspace),
                mode,
                "repo",
                "add",
                supplied,
            )
            assert machine.returncode != 0
            assert '"code"' in machine.stdout
            assert supplied in machine.stdout
            assert str((tmp_path / supplied).resolve()) in machine.stdout

    for driver in _drivers():
        result = _run_driver(
            driver, tmp_path, "--root", str(tmp_path / "@root"), "add", "README.md"
        )
        assert result.returncode != 0
        assert "--target @root" in result.stderr


def test_add_and_diff_route_member_operands_from_explicit_root(tmp_path: Path) -> None:
    for index, driver in enumerate(_drivers()):
        workspace = tmp_path / f"routing-{index}" / "Harbor Workspace"
        native = native_client(workspace)
        asyncio.run(native.create_workspace(workspace_id=f"ws_routing_{index}"))
        asyncio.run(
            native.create_repo("members/app", member_id="mem_app", source_id="src_app")
        )
        member = workspace / "members/app"
        commit = create_git_repo(member) if not (member / ".git").exists() else None
        if commit is None:
            git(member, "config", "user.name", "GWZ Test")
            git(member, "config", "user.email", "gwz@example.invalid")
            (member / "README.md").write_text("base\n", encoding="utf-8")
            git(member, "add", "README.md")
            git(member, "commit", "-m", "initial")
        (member / "README.md").write_text("staged\n", encoding="utf-8")

        staged = _run_driver(driver, member, "--root", "../..", "add", "README.md")
        assert staged.returncode == 0, staged.stderr
        assert "M  README.md" in git(member, "status", "--porcelain")
        (member / "README.md").write_text("unstaged\n", encoding="utf-8")
        diffed = _run_driver(driver, member, "--root", "../..", "diff", "README.md")
        assert diffed.returncode == 0, diffed.stderr

        outside = workspace.parent / "executor-b"
        outside.mkdir()
        absolute = _run_driver(
            driver, outside, "--root", str(workspace), "add", str(member / "README.md")
        )
        assert absolute.returncode == 0, absolute.stderr
        escaped = _run_driver(driver, workspace, "--root", ".", "add", "../escape")
        assert escaped.returncode != 0


def test_dirty_registration_status_sync_and_no_commit_agree_across_drivers(tmp_path: Path) -> None:
    rust_bin = os.environ.get("GWZ_RUST_BIN")
    if not rust_bin:
        pytest.skip("run_tests.py provides the matching Rust CLI")
    workspace = tmp_path / "Harbor Workspace"
    client = native_client(workspace)
    asyncio.run(client.create_workspace(workspace_id="ws_feedback_parity"))
    repo = workspace / "member"
    before = create_git_repo(repo)
    (repo / "README.md").write_text("uncommitted work to preserve\n", encoding="utf-8")

    added = asyncio.run(client.add_existing_repo(repo))
    with pytest.raises(GwzOperationError) as observed:
        asyncio.run(client.status(paths=["member"]))
    assert observed.value.aggregate_status is AggregateStatus.dirty
    status = observed.value.response
    synced = asyncio.run(client.repo_sync("member"))
    for response in (added, status, synced):
        row = response.response.members[0]
        assert row.lock_match is LockMatch.differs
        assert row.lock_difference_reasons == [LockDifferenceReason.dirty_worktree]
    assert synced.response.meta.aggregate_status is AggregateStatus.noop
    assert "does not change worktree" in synced.response.meta.message

    def run(driver: list[str], *args: str) -> str:
        result = subprocess.run(
            [*driver, "--root", str(workspace), *args],
            check=False, capture_output=True, text=True,
        )
        assert result.returncode in ((0, 1) if "status" in args else (0,)), result.stderr
        return result.stdout

    rust = [rust_bin]
    python = [sys.executable, "-m", "gwz.cli"]
    for driver in (rust, python):
        human = run(driver, "--target", "member", "status")
        assert "uncommitted work differs from the locked commit" in human
        noop = run(driver, "--target", "member", "commit", "-m", "must not create a commit")
        assert "No commit was created" in noop
    for mode in ("--json", "--jsonl"):
        document = json.loads(run(rust, "--target", "member", mode, "status"))
        row = document["members"][0]
        assert row["lock_match"] == "Differs"
        assert row["lock_difference_reasons"] == ["DirtyWorktree"]
    assert git(repo, "rev-parse", "HEAD") == before
    assert (repo / "README.md").read_text(encoding="utf-8") == "uncommitted work to preserve\n"
