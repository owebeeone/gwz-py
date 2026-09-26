"""A reader sees every event sequence while members emit from parallel threads.

gwz-core numbers and delivers each event under one lock, so the native store
holds events in sequence order and the bridge's reader, which skips any event
numbered below one it has already yielded, never loses one.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from gwz.protocol.generated import EventKind, OperationEvent
from native_helpers import commit_file, git, init_bare_repo, native_client

MEMBERS = 8


def workspace_with_remotes(root: Path, remotes: Path) -> None:
    client = native_client(root)
    asyncio.run(client.create_workspace(workspace_id="ws_event_order"))
    git(root, "config", "user.name", "GWZ Test")
    git(root, "config", "user.email", "gwz@example.invalid")
    for index in range(MEMBERS):
        path = f"repos/m{index}"
        asyncio.run(client.create_repo(path, member_id=f"mem_m{index}", source_id=f"src_m{index}"))
        repo = root / path
        commit_file(repo, "README.md", f"{index}\n", "initial")
        remote = remotes / f"m{index}.git"
        init_bare_repo(remote)
        git(repo, "remote", "add", "origin", str(remote))
        git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
        asyncio.run(client.repo_sync(path))


def test_reader_sees_every_sequence_while_members_fetch_in_parallel(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    remotes = tmp_path / "remotes"
    root.mkdir()
    remotes.mkdir()
    workspace_with_remotes(root, remotes)
    client = native_client(root)

    async def collect() -> list[OperationEvent]:
        return [event async for event in client.fetch_stream()]

    members = {f"mem_m{index}" for index in range(MEMBERS)}
    for _ in range(5):
        events = asyncio.run(collect())
        sequences = [event.sequence for event in events]
        assert sequences == list(range(len(sequences))), sequences
        started = {event.member_id for event in events if event.kind is EventKind.member_started}
        finished = {event.member_id for event in events if event.kind is EventKind.member_finished}
        assert members <= started
        assert started == finished
