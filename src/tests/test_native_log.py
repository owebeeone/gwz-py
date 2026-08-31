"""Focused real-workspace proofs for the S3.5 native log protocol path."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import gwz.cli as cli_module
from gwz.errors import GwzBridgeError
from gwz.protocol.generated import (
    AggregateStatus,
    LogMergeKind,
    LogOutputRecordKind,
)

from native_helpers import commit_file, create_workspace_with_member, git, native_client


def test_native_log_dispatch_streams_records_and_releases_spool(tmp_path: Path) -> None:
    repo, first_commit = create_workspace_with_member(tmp_path)
    second_commit = commit_file(repo, "second.txt", "two\n", "second")
    client = native_client(tmp_path)

    async def run() -> tuple[object, list[object]]:
        response = await client.log(max_entries=0, no_merges=True, coalesce=False)
        records = [record async for record in client.log_output(response.output)]
        return response, records

    response, records = asyncio.run(run())

    assert response.response.meta.aggregate_status is AggregateStatus.ok
    entries = [record.entry for record in records if record.kind is LogOutputRecordKind.entry]
    commits = {
        member.commit
        for entry in entries
        if entry is not None
        for member in entry.members
    }
    assert {first_commit, second_commit} <= commits

    async def read_released() -> None:
        await client.bridge.log_output_read(response.output.log_id)

    with pytest.raises(GwzBridgeError) as exc_info:
        asyncio.run(read_released())
    assert exc_info.value.code == "InvalidRequest"


def test_native_log_preserves_marker_invalid_wire_pair(tmp_path: Path) -> None:
    repo, _commit = create_workspace_with_member(tmp_path)
    (repo / "invalid.txt").write_text("invalid\n", encoding="utf-8")
    git(repo, "add", "invalid.txt")
    git(repo, "commit", "-m", "invalid marker\n\nGWZ-Commit-ID = not-a-uuid")
    invalid_commit = git(repo, "rev-parse", "HEAD")
    client = native_client(tmp_path)

    async def run():
        response = await client.log(max_entries=0)
        return [record async for record in client.log_output(response.output)]

    records = asyncio.run(run())
    entry = next(
        record.entry
        for record in records
        if record.entry is not None
        and any(member.commit == invalid_commit for member in record.entry.members)
    )
    assert entry.provenance.kind is LogMergeKind.none
    assert entry.provenance.gwz_commit_id == "marker-invalid"


def test_native_log_partial_keeps_degradation_records_for_client(tmp_path: Path) -> None:
    repo, _commit = create_workspace_with_member(tmp_path)
    commit_file(tmp_path, "root.txt", "root\n", "root contribution")
    repo.rename(tmp_path / "repos" / "app-away")
    client = native_client(tmp_path)

    async def run():
        response = await client.log(max_entries=0)
        records = [record async for record in client.log_output(response.output)]
        return response, records

    response, records = asyncio.run(run())

    assert response.response.meta.aggregate_status is AggregateStatus.partial
    degradations = [
        record.degradation
        for record in records
        if record.kind is LogOutputRecordKind.degradation
    ]
    assert any(
        degradation is not None and degradation.member_id == "mem_app"
        for degradation in degradations
    )


def test_native_cli_log_maps_core_rejection_to_exit_two(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    create_workspace_with_member(tmp_path)

    exit_code = cli_module.main(["--root", str(tmp_path), "log", "+missing"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "SnapshotNotFound" in captured.err


def test_native_cli_log_maps_partial_and_strict_failed_to_exit_one(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, _commit = create_workspace_with_member(tmp_path)
    commit_file(tmp_path, "root.txt", "root\n", "root contribution")
    repo.rename(tmp_path / "repos" / "app-away")

    assert cli_module.main(["--root", str(tmp_path), "log"]) == 1
    assert capsys.readouterr() == ("", "")
    assert cli_module.main(["--root", str(tmp_path), "log", "--strict"]) == 1
    assert capsys.readouterr() == ("", "")
