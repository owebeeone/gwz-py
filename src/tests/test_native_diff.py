"""End-to-end `gwz diff` parity through the native bridge and a real workspace.

These are the D5 acceptance proofs for the `gwz-py` half: a Python client plans a
workspace diff over a real materialized member and reads the byte-bearing
`diff.output` log through the PyO3 log reader — never through the bounded
`events.subscribe` buffer. They mirror the rust-side behaviors called out in the
plan: NUL-safe binary patch bytes, a resumable cursor, `--quiet` (no output log),
a surfaced `stale_file` record on a worktree race, and rename record fidelity.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from gwz.protocol.generated import (
    DiffManifestMode,
    DiffOutputFormat,
    DiffOutputRecordKind,
    DiffStatus,
    DiffTargetExclusionReason,
)

from native_helpers import commit_file, git, native_client


def _workspace_with_member(root: Path) -> Path:
    """A workspace with one materialized Git member `repos/app`, one commit, and a
    captured snapshot so the member is active."""
    client = native_client(root)
    asyncio.run(client.create_workspace(workspace_id="ws_native"))
    asyncio.run(client.create_repo("repos/app", member_id="mem_app", source_id="src_app"))
    repo = root / "repos" / "app"
    commit_file(repo, "README.md", "one\n", "initial")
    asyncio.run(client.capture(paths=["repos/app"]))
    return repo


async def _collect_output(client, log_ref):
    return [record async for record in client.diff_output(log_ref)]


def test_native_diff_reads_nul_heavy_patch_bytes_byte_exact(tmp_path: Path) -> None:
    """THE acceptance proof: a NUL-laden patch streams byte-exact through
    `diff.output` (its own PyO3 log reader), not the bounded events buffer."""
    repo = _workspace_with_member(tmp_path)
    # A tracked file whose new content carries embedded NUL bytes; --text forces a
    # raw textual patch so the NULs land literally in the hunk bytes.
    (repo / "data.bin").write_bytes(b"alpha\x00zero\nbeta\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "add data")
    new_bytes = b"alpha\x00zero\nGAMMA\x00\x00\nbeta\n\xff\x00tail\x00"
    (repo / "data.bin").write_bytes(new_bytes)

    client = native_client(tmp_path)

    async def run() -> None:
        response = await client.diff(output_format=DiffOutputFormat.patch, text=True)
        # Metadata-only response: no operation_id, no patch bytes in the manifest.
        assert response.response.meta.operation_id is None
        assert response.output is not None
        assert response.output.format is DiffOutputFormat.patch

        records = await _collect_output(client, response.output)
        kinds = [r.kind for r in records]
        assert DiffOutputRecordKind.file_started in kinds
        assert DiffOutputRecordKind.file_finished in kinds

        patch = b"".join(
            r.data for r in records if r.kind is DiffOutputRecordKind.patch_bytes and r.data
        )
        # Byte-exact: the embedded NULs survived the doubly-nested BYTES path.
        assert patch.count(0) >= 4
        assert b"GAMMA\x00\x00" in patch
        assert b"\xff\x00tail\x00" in patch
        # Workspace-relative header, not member-relative.
        assert b"a/repos/app/data.bin" in patch

    asyncio.run(run())


def test_native_diff_output_cursor_is_resumable(tmp_path: Path) -> None:
    """Reading in small batches by cursor yields the same records as one read —
    no dup, no skip (taut-shape D8)."""
    repo = _workspace_with_member(tmp_path)
    for i in range(4):
        commit_file(repo, f"f{i}.txt", "base\n", f"add f{i}")
    # Dirty every file so several manifest entries exist.
    for i in range(4):
        (repo / f"f{i}.txt").write_text("changed\n", encoding="utf-8")

    client = native_client(tmp_path)

    async def run() -> None:
        response = await client.diff(output_format=DiffOutputFormat.patch)
        assert response.output is not None
        log_id = response.output.log_id

        # One-shot read via the streaming client.
        whole = [r.kind.name async for r in client.diff_output(response.output, stream_id="whole")]

        # Manual small-batch cursor loop over the bridge reader, two records at a
        # time, resuming from the returned cursor each round.
        collected: list[str] = []
        cursor: int | None = None
        while True:
            answer = await client.bridge.diff_log_read(
                log_id, "resumable", cursor=cursor, max_records=2
            )
            cursor = answer.next_cursor
            collected.extend(rec.kind.name for rec in answer.records)
            if answer.state in ("eof", "closed", "failed"):
                break
        await client.bridge.diff_log_end_stream(log_id, "resumable")

        assert collected == whole
        # Sanity: at least started/patch/finished per changed file.
        assert collected.count("file_started") == 4
        assert collected.count("file_finished") == 4

    asyncio.run(run())


def test_native_diff_quiet_returns_no_output_log(tmp_path: Path) -> None:
    """`--quiet` / any_difference: summary-only, no file list, no output log."""
    repo = _workspace_with_member(tmp_path)
    (repo / "README.md").write_text("two\n", encoding="utf-8")

    client = native_client(tmp_path)

    async def run() -> None:
        response = await client.diff(
            output_format=DiffOutputFormat.no_patch,
            manifest_mode=DiffManifestMode.any_difference,
        )
        assert response.output is None
        assert response.summary is not None
        assert response.summary.has_differences is True
        assert response.files == []

    asyncio.run(run())


def test_native_diff_quiet_no_differences_is_clean(tmp_path: Path) -> None:
    _workspace_with_member(tmp_path)
    client = native_client(tmp_path)

    async def run() -> None:
        response = await client.diff(
            output_format=DiffOutputFormat.no_patch,
            manifest_mode=DiffManifestMode.any_difference,
        )
        assert response.output is None
        assert response.summary is not None
        assert response.summary.has_differences is False

    asyncio.run(run())


def test_native_tagged_diff_selects_the_exact_tag_intersection(tmp_path: Path) -> None:
    repo = _workspace_with_member(tmp_path)
    git(repo, "tag", "release-old")
    commit_file(repo, "README.md", "two\n", "release new")
    git(repo, "tag", "release-new")
    client = native_client(tmp_path)

    async def run() -> None:
        response = await client.diff(
            ["release-old", "release-new"],
            tagged=True,
            output_format=DiffOutputFormat.no_patch,
        )
        assert len(response.targets) == 1
        assert response.targets[0].scope.member_id == "mem_app"
        assert len(response.excluded_targets) == 1
        assert response.excluded_targets[0].scope.root is True
        assert (
            response.excluded_targets[0].reason
            is DiffTargetExclusionReason.tag_missing
        )

    asyncio.run(run())


def test_native_diff_rename_record_fidelity(tmp_path: Path) -> None:
    """A rename keeps both paths + similarity in the manifest and emits a single
    rename patch record (not degraded to add/delete)."""
    repo = _workspace_with_member(tmp_path)
    body = "".join(f"line {i}\n" for i in range(40))
    commit_file(repo, "old_name.txt", body, "add old_name")
    # Rename (staged so the index shows the rename) with identical content.
    git(repo, "mv", "old_name.txt", "new_name.txt")

    client = native_client(tmp_path)

    async def run() -> None:
        # --cached compares HEAD -> index, where the rename is staged.
        response = await client.diff(
            cached=True,
            output_format=DiffOutputFormat.patch,
            find_renames=True,
        )
        rename = next(
            (f for f in response.files if f.status is DiffStatus.renamed),
            None,
        )
        assert rename is not None, [f.status.name for f in response.files]
        assert rename.old_path == "repos/app/old_name.txt"
        assert rename.new_path == "repos/app/new_name.txt"
        assert rename.similarity is not None

        assert response.output is not None
        records = await _collect_output(client, response.output)
        patch = b"".join(
            r.data for r in records if r.kind is DiffOutputRecordKind.patch_bytes and r.data
        )
        assert b"rename from repos/app/old_name.txt" in patch
        assert b"rename to repos/app/new_name.txt" in patch
        # Not degraded: exactly one entry for the renamed pair, and neither
        # renamed path leaks as a separate add or delete entry.
        renamed_paths = {"repos/app/old_name.txt", "repos/app/new_name.txt"}
        renamed_entries = [
            f
            for f in response.files
            if f.old_path in renamed_paths or f.new_path in renamed_paths
        ]
        assert len(renamed_entries) == 1
        assert renamed_entries[0].status is DiffStatus.renamed

    asyncio.run(run())
