from __future__ import annotations

import asyncio
from pathlib import Path

from gwz.protocol.generated import AggregateStatus, MemberStatus, PlannedAction

from native_helpers import commit_file, create_git_repo, git, native_client


def test_native_create_workspace_and_empty_status(tmp_path: Path) -> None:
    client = native_client(tmp_path)

    response = asyncio.run(client.create_workspace(workspace_id="ws_native"))

    assert response.response.meta.aggregate_status is AggregateStatus.ok
    assert (tmp_path / "gwz.conf" / "gwz.yml").is_file()
    assert (tmp_path / "gwz.conf" / "gwz.lock.yml").is_file()

    status = asyncio.run(client.status())

    assert status.response.meta.aggregate_status is AggregateStatus.ok
    assert status.response.members == []


def test_native_create_repo_status_and_repo_sync_dry_run(tmp_path: Path) -> None:
    client = native_client(tmp_path)
    asyncio.run(client.create_workspace(workspace_id="ws_native"))

    created = asyncio.run(
        client.create_repo("repos/app", member_id="mem_app", source_id="src_app")
    )

    assert created.response.meta.aggregate_status is AggregateStatus.ok
    created_member = created.response.members[0]
    assert created_member.member_id == "mem_app"
    assert created_member.member_path == "repos/app"
    assert created_member.status is MemberStatus.ok
    assert (tmp_path / "repos" / "app" / ".git").is_dir()

    status = asyncio.run(client.status())
    assert status.response.meta.aggregate_status is AggregateStatus.ok
    assert status.response.members[0].member_id == "mem_app"
    assert status.response.members[0].status is MemberStatus.ok

    repo = tmp_path / "repos" / "app"
    commit_file(repo, "README.md", "one\n", "initial")
    git(repo, "remote", "add", "origin", "https://example.invalid/org/app.git")

    synced = asyncio.run(client.repo_sync("repos/app", dry_run=True))

    assert synced.response.meta.aggregate_status is AggregateStatus.accepted
    synced_member = synced.response.members[0]
    assert synced_member.status is MemberStatus.planned
    assert synced_member.planned is not None


def test_native_add_existing_repo_records_git_state(tmp_path: Path) -> None:
    client = native_client(tmp_path)
    asyncio.run(client.create_workspace(workspace_id="ws_native"))
    repo = tmp_path / "local-repo"
    commit = create_git_repo(repo)

    added = asyncio.run(client.add_existing_repo(repo))

    assert added.response.meta.aggregate_status is AggregateStatus.ok
    member = added.response.members[0]
    assert member.status is MemberStatus.ok
    assert member.member_path == "local-repo"
    assert member.state is not None
    assert member.state.commit == commit

    status = asyncio.run(client.status(paths=["local-repo"]))

    assert status.response.meta.aggregate_status is AggregateStatus.ok
    assert status.response.members[0].member_path == "local-repo"


def test_native_init_from_sources_dry_run_plans_without_cloning(tmp_path: Path) -> None:
    client = native_client(tmp_path)

    response = asyncio.run(
        client.init_from_sources(
            ["https://example.invalid/org/repo-a.git"],
            workspace_id="ws_native",
            dry_run=True,
        )
    )

    assert response.response.meta.aggregate_status is AggregateStatus.accepted
    member = response.response.members[0]
    assert member.status is MemberStatus.planned
    assert member.planned is not None
    assert member.planned.action is PlannedAction.clone
    assert not (tmp_path / "gwz.conf" / "gwz.yml").exists()
