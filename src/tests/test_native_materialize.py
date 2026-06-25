from __future__ import annotations

import asyncio
from pathlib import Path

from gwz.protocol.generated import AggregateStatus, MemberStatus, PlannedAction

from native_helpers import commit_file, create_workspace_with_member, git, native_client


def test_native_materialize_lock_dry_run(tmp_path: Path) -> None:
    create_workspace_with_member(tmp_path)
    client = native_client(tmp_path)

    response = asyncio.run(client.materialize("lock", dry_run=True, paths=["repos/app"]))

    assert response.response.meta.aggregate_status is AggregateStatus.accepted
    assert response.response.members[0].member_id == "mem_app"


def test_native_snapshot_sources_and_branch_switch(tmp_path: Path) -> None:
    repo, main_commit = create_workspace_with_member(tmp_path)
    client = native_client(tmp_path)
    git(repo, "checkout", "-B", "feature")
    feature_commit = commit_file(repo, "FEATURE.md", "feature\n", "feature")
    git(repo, "checkout", "main")

    current_snapshot = asyncio.run(
        client.snapshot("snap_current", current_branch=True, paths=["repos/app"])
    )
    branch_snapshot = asyncio.run(client.snapshot("snap_feature", branch="feature", paths=["repos/app"]))

    assert current_snapshot.response.meta.aggregate_status is AggregateStatus.ok
    assert current_snapshot.response.members[0].state is not None
    assert current_snapshot.response.members[0].state.commit == main_commit
    assert branch_snapshot.response.meta.aggregate_status is AggregateStatus.ok
    assert branch_snapshot.response.members[0].state is not None
    assert branch_snapshot.response.members[0].state.commit == feature_commit

    switched = asyncio.run(client.materialize("branch", name="feature", paths=["repos/app"]))

    assert switched.response.meta.aggregate_status is AggregateStatus.ok
    switched_member = switched.response.members[0]
    assert switched_member.status is MemberStatus.ok
    assert switched_member.state is not None
    assert switched_member.state.branch == "feature"
    assert switched_member.state.commit == feature_commit
    assert git(repo, "branch", "--show-current") == "feature"


def test_native_tag_create_list_delete(tmp_path: Path) -> None:
    create_workspace_with_member(tmp_path)
    client = native_client(tmp_path)

    created = asyncio.run(client.tag("v1"))
    listed = asyncio.run(client.tag(op="list"))
    deleted = asyncio.run(client.tag("v1", op="delete"))
    listed_after_delete = asyncio.run(client.tag(op="list"))

    assert created.response.meta.aggregate_status is AggregateStatus.ok
    assert listed.response.meta.aggregate_status is AggregateStatus.ok
    assert listed.tags is not None
    assert any(tag.name == "v1" and tag.members >= 1 for tag in listed.tags)
    assert deleted.response.meta.aggregate_status is AggregateStatus.ok
    assert listed_after_delete.tags is not None
    assert all(tag.name != "v1" for tag in listed_after_delete.tags)


def test_native_capture_records_observed_commit(tmp_path: Path) -> None:
    repo, _ = create_workspace_with_member(tmp_path)
    client = native_client(tmp_path)
    observed = commit_file(repo, "README.md", "two\n", "second")

    response = asyncio.run(client.capture(paths=["repos/app"]))

    assert response.response.meta.aggregate_status is AggregateStatus.ok
    member = response.response.members[0]
    assert member.status is MemberStatus.ok
    assert member.state is not None
    assert member.state.commit == observed
    assert member.planned is None or member.planned.action is PlannedAction.noop
