from __future__ import annotations

import asyncio
from pathlib import Path

from gwz.protocol.generated import AggregateStatus, MemberStatus, PlannedAction

from native_helpers import (
    bare_ref,
    commit_file,
    create_workspace_with_member,
    git,
    init_bare_repo,
    native_client,
)


def test_native_stage_and_commit_member_changes(tmp_path: Path) -> None:
    repo, before = create_workspace_with_member(tmp_path)
    client = native_client(tmp_path)
    git(tmp_path, "config", "user.name", "GWZ Test")
    git(tmp_path, "config", "user.email", "gwz@example.invalid")
    (repo / "work.txt").write_text("work\n", encoding="utf-8")

    staged = asyncio.run(client.stage(["repos/app/work.txt"], cwd=tmp_path))

    assert staged.response.meta.aggregate_status is AggregateStatus.ok
    assert "A  work.txt" in git(repo, "status", "--porcelain")

    committed = asyncio.run(client.commit("add work", paths=["repos/app"]))
    after = git(repo, "rev-parse", "HEAD")

    assert committed.response.meta.aggregate_status is AggregateStatus.ok
    assert after != before
    assert committed.response.members[0].state is not None
    assert committed.response.members[0].state.commit == after
    assert git(repo, "status", "--porcelain") == ""


def test_native_pull_snapshot_restores_snapshot_commit(tmp_path: Path) -> None:
    repo, first = create_workspace_with_member(tmp_path)
    client = native_client(tmp_path)
    asyncio.run(client.snapshot("snap_one", paths=["repos/app"]))
    second = commit_file(repo, "README.md", "two\n", "second")
    asyncio.run(client.capture(paths=["repos/app"]))

    pulled = asyncio.run(client.pull_snapshot("snap_one", paths=["repos/app"]))

    assert second != first
    assert pulled.response.meta.aggregate_status is AggregateStatus.ok
    assert pulled.response.members[0].state is not None
    assert pulled.response.members[0].state.commit == first
    assert git(repo, "rev-parse", "HEAD") == first


def test_native_pull_head_dry_run_noops_without_fetch_remote(tmp_path: Path) -> None:
    create_workspace_with_member(tmp_path)
    client = native_client(tmp_path)

    pulled = asyncio.run(client.pull_head(dry_run=True, paths=["repos/app"]))

    assert pulled.response.meta.aggregate_status is AggregateStatus.noop
    member = pulled.response.members[0]
    assert member.status is MemberStatus.noop
    assert member.planned is not None
    assert member.planned.action is PlannedAction.noop


def test_native_push_dry_run_and_push_to_local_bare_remote(tmp_path: Path) -> None:
    repo, commit = create_workspace_with_member(tmp_path)
    client = native_client(tmp_path)
    remote = tmp_path / "origin.git"
    init_bare_repo(remote)
    git(repo, "remote", "add", "origin", str(remote))
    asyncio.run(client.repo_sync("repos/app"))

    dry_run = asyncio.run(client.push(dry_run=True, paths=["repos/app"]))
    pushed = asyncio.run(client.push(paths=["repos/app"]))

    assert dry_run.response.meta.aggregate_status is AggregateStatus.ok
    assert dry_run.response.members[0].status is MemberStatus.planned
    assert dry_run.response.members[0].planned is not None
    assert dry_run.response.members[0].planned.action is PlannedAction.push
    assert pushed.response.meta.aggregate_status is AggregateStatus.ok
    assert pushed.response.members[0].status is MemberStatus.ok
    assert bare_ref(remote, "refs/heads/main") == commit
