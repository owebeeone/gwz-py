from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gwz.errors import GwzBridgeError
from gwz.protocol.generated import ActionKind, AggregateStatus, EventKind, OperationEvent

from native_helpers import (
    commit_file,
    create_workspace_with_member,
    git,
    init_bare_repo,
    native_client,
)


async def collect(events: AsyncIterator[OperationEvent]) -> list[OperationEvent]:
    return [event async for event in events]


def test_native_operation_events_and_result_round_trip(tmp_path: Path) -> None:
    repo, commit = create_workspace_with_member(tmp_path)
    client = native_client(tmp_path)
    remote = tmp_path / "origin.git"
    init_bare_repo(remote)
    git(repo, "remote", "add", "origin", str(remote))
    asyncio.run(client.repo_sync("repos/app"))

    response = asyncio.run(client.push(paths=["repos/app"]))
    operation_id = response.response.meta.operation_id
    assert operation_id is not None

    events = asyncio.run(collect(client.events(operation_id)))
    result = asyncio.run(client.operation_result(operation_id))

    assert events
    assert events[0].kind is EventKind.operation_started
    assert events[-1].kind is EventKind.operation_finished
    assert [event.sequence for event in events] == sorted(event.sequence for event in events)
    assert all(event.operation_id == operation_id for event in events)
    assert result.operation_id == operation_id
    assert result.request_id == response.response.meta.request_id
    assert result.aggregate_status is AggregateStatus.ok
    assert result.members == response.response.members
    assert result.started_at_ms <= result.finished_at_ms
    assert git(repo, "rev-parse", "HEAD") == commit


def test_native_stream_helper_drains_events_and_result(tmp_path: Path) -> None:
    repo, _ = create_workspace_with_member(tmp_path)
    client = native_client(tmp_path)
    remote = tmp_path / "origin.git"
    init_bare_repo(remote)
    git(repo, "remote", "add", "origin", str(remote))
    asyncio.run(client.repo_sync("repos/app"))

    events = asyncio.run(collect(client.push_stream(paths=["repos/app"])))

    assert events
    assert events[0].kind is EventKind.operation_started
    assert events[-1].kind is EventKind.operation_finished


def test_native_stream_yields_before_operation_result_is_ready(tmp_path: Path, monkeypatch) -> None:
    repo, _ = create_workspace_with_member(tmp_path)
    client = native_client(tmp_path)
    remote = tmp_path / "origin.git"
    init_bare_repo(remote)
    monkeypatch.setenv("GWZ_PY_TEST_EVENT_DELAY_MS", "500")
    git(repo, "remote", "add", "origin", str(remote))
    asyncio.run(client.repo_sync("repos/app"))

    async def observe() -> tuple[list[OperationEvent], bool]:
        stream = client.push_stream(paths=["repos/app"])
        iterator = stream.__aiter__()
        first = await asyncio.wait_for(iterator.__anext__(), timeout=1.0)
        result_task = asyncio.create_task(client.operation_result(first.operation_id))
        await asyncio.sleep(0.1)
        result_ready_after_first_event = result_task.done()
        events = [first]
        async for event in iterator:
            events.append(event)
        await result_task
        return events, result_ready_after_first_event

    events, result_ready_after_first_event = asyncio.run(observe())

    assert events[0].kind is EventKind.operation_started
    assert events[-1].kind is EventKind.operation_finished
    assert not result_ready_after_first_event


def test_native_clone_workspace_streams_root_and_member_events(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    client = native_client(source)
    asyncio.run(client.create_workspace(workspace_id="ws_native_clone"))
    asyncio.run(client.create_repo("repos/app", member_id="mem_app", source_id="src_app"))
    member_repo = source / "repos" / "app"
    commit = commit_file(member_repo, "README.md", "one\n", "initial")

    member_remote = tmp_path / "member.git"
    init_bare_repo(member_remote)
    git(member_repo, "remote", "add", "origin", str(member_remote))
    git(member_repo, "push", "origin", "HEAD:refs/heads/main")
    asyncio.run(client.repo_sync("repos/app"))
    asyncio.run(client.capture(paths=["repos/app"]))

    git(source, "config", "user.name", "GWZ Test")
    git(source, "config", "user.email", "gwz@example.invalid")
    git(source, "add", "gwz.conf")
    git(source, "commit", "-m", "workspace")

    target = tmp_path / "clone"
    clone_client = native_client(tmp_path)
    events = asyncio.run(
        collect(
            clone_client.clone_workspace_stream(
                str(source),
                target,
                workspace_id="ws_native_clone",
            )
        )
    )
    result = asyncio.run(clone_client.operation_result(events[0].operation_id))

    assert events[0].kind is EventKind.operation_started
    assert events[-1].kind is EventKind.operation_finished
    assert [event.sequence for event in events] == list(range(len(events)))
    assert any(
        event.kind is EventKind.member_started and event.member_path == str(target)
        for event in events
    )
    assert any(
        event.kind is EventKind.member_started and event.member_path == "repos/app"
        for event in events
    )
    assert result.action is ActionKind.clone_workspace
    assert result.aggregate_status is AggregateStatus.ok
    assert (target / "gwz.conf" / "gwz.lock.yml").is_file()
    assert git(target / "repos" / "app", "rev-parse", "HEAD") == commit


def test_native_operation_lookup_reports_missing_id(tmp_path: Path) -> None:
    client = native_client(tmp_path)

    with pytest.raises(GwzBridgeError, match="operation missing not found"):
        asyncio.run(client.operation_result("missing"))

    with pytest.raises(GwzBridgeError, match="operation missing not found"):
        asyncio.run(collect(client.events("missing")))
