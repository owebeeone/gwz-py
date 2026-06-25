from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from gwz.errors import GwzBridgeError
from gwz.protocol.generated import AggregateStatus, EventKind, OperationEvent

from native_helpers import (
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


def test_native_operation_lookup_reports_missing_id(tmp_path: Path) -> None:
    client = native_client(tmp_path)

    with pytest.raises(GwzBridgeError, match="operation missing not found"):
        asyncio.run(client.operation_result("missing"))

    with pytest.raises(GwzBridgeError, match="operation missing not found"):
        asyncio.run(collect(client.events("missing")))
