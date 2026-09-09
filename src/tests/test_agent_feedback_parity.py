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
