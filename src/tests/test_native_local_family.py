"""The local clone family through the native bridge (LCM1.2, lane C).

`test_cli_local_family.py` pins what the Python driver *sends* against the
cross-driver parity fixture; this file runs the served verbs against real
core: a verbatim `local clone`, work committed in the clone, and
`merge --remote A` integrating it through the retained import ref -- the
same loop `gwz-cli/tests/local_family_workflows.rs` drives through the Rust
binary, so the two drivers are compared on the same outcome.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gwz.errors import GwzBridgeError
from gwz.protocol.generated import (
    AggregateStatus,
    LocalFamilyOp,
    MergeOp,
    MergeOperationState,
    MergeParticipantState,
)

from native_helpers import (
    commit_file,
    create_workspace_with_member,
    git,
    native_client,
    native_module,
)


def test_a_family_merge_by_name_integrates_the_clones_commits(tmp_path: Path) -> None:
    native_module()
    root = tmp_path / "root"
    member, base = create_workspace_with_member(root)
    git(root, "add", "-A")
    git(root, "commit", "-m", "init workspace")
    client = native_client(root)

    dest = tmp_path / "root-A"
    created = asyncio.run(client.clone_local_workspace("A", dest))
    assert created.response.meta.aggregate_status is AggregateStatus.ok
    assert "created local clone `A`" in (created.response.meta.message or "")
    clone_member = dest / "repos" / "app"
    assert git(clone_member, "rev-parse", "HEAD") == base

    work = commit_file(clone_member, "feature.txt", "from A\n", "work in A")
    root_work = commit_file(dest, "root-work.txt", "root work\n", "root work in A")
    merged = asyncio.run(client.merge(local_source_name="A"))
    assert merged.response.meta.aggregate_status is AggregateStatus.ok
    assert merged.state is MergeOperationState.completed
    assert merged.open is False
    assert {repo.target_id for repo in merged.repos} == {"@root", "mem_app"}
    root_repo = next(repo for repo in merged.repos if repo.target_id == "@root")
    assert root_repo.source_commit == root_work
    repo = next(repo for repo in merged.repos if repo.target_id == "mem_app")
    assert repo.target_id == "mem_app"
    assert repo.state is MergeParticipantState.fast_forwarded
    assert repo.source_ref.startswith("refs/gwz/local-imports/xfer_")
    assert repo.source_commit == work
    assert repo.resulting_commit == work
    message = merged.response.meta.message or ""
    assert f"imported HEAD of family member `A` as {repo.source_ref}" in message
    assert f"mem_app={work}" in message and f"@root={root_work}" in message
    assert git(member, "rev-parse", "HEAD") == work
    assert git(member, "rev-parse", repo.source_ref) == work, "the import ref is retained"
    assert git(member, "remote") == "", "no family remote is persisted"

    # A retry after more work mints a fresh transfer id; the earlier ref stays.
    more = commit_file(clone_member, "feature.txt", "more from A\n", "more work in A")
    again = asyncio.run(client.merge(op=MergeOp.start, local_source_name="A"))
    assert again.state is MergeOperationState.completed
    repo_again = next(repo for repo in again.repos if repo.target_id == "mem_app")
    assert repo_again.source_ref != repo.source_ref
    assert git(member, "rev-parse", "HEAD") == more
    assert git(member, "rev-parse", repo.source_ref) == work
    assert git(member, "rev-parse", repo_again.source_ref) == more
    asyncio.run(client.local_family(LocalFamilyOp.dispose, name="A"))
    assert not dest.exists()


def test_ordinary_dispose_deletes_a_preserved_lane_and_refuses_unique_history(
    tmp_path: Path,
) -> None:
    """LCM2.1/LCM2.2 through the native bridge (design §5, §12).

    A clean lane whose every protected root the root holds is deleted by a
    plain dispose; a lane holding a commit nothing else holds refuses
    `unwaived_hazard` with its directory intact, and deletes once the
    operator names the loss -- the same loop
    `gwz-cli/tests/local_family_workflows.rs` drives through the binary.
    """

    native_module()
    root = tmp_path / "root"
    member, _base = create_workspace_with_member(root)
    git(root, "add", "-A")
    git(root, "commit", "-m", "init workspace")
    client = native_client(root)

    dest = tmp_path / "root-A"
    created = asyncio.run(client.clone_local_workspace("A", dest))
    assert created.response.meta.aggregate_status is AggregateStatus.ok
    deleted = asyncio.run(client.local_family(LocalFamilyOp.dispose, name="A"))
    assert deleted.response.meta.aggregate_status is AggregateStatus.ok
    message = deleted.response.meta.message or ""
    assert "deleted local clone `A`" in message and "its row removed" in message
    assert not dest.exists(), "the directory is gone"

    dest_b = tmp_path / "root-B"
    asyncio.run(client.clone_local_workspace("B", dest_b))
    unique = commit_file(dest_b / "repos" / "app", "feature.txt", "from B\n", "only in B")
    # The native bridge raises a core refusal with its typed code label
    # ahead of core's message, unedited.
    with pytest.raises(GwzBridgeError) as refused:
        asyncio.run(client.local_family(LocalFamilyOp.dispose, name="B"))
    message = str(refused.value)
    assert "UnwaivedHazard: local dispose `B`" in message
    assert "<unpreserved-history>" in message and unique in message
    assert "nothing was removed" in message
    assert (dest_b / "repos" / "app" / "feature.txt").is_file(), "nothing was removed"

    forced = asyncio.run(
        client.local_family(
            LocalFamilyOp.dispose, name="B", force_hazards=["unpreserved-history"]
        )
    )
    assert forced.response.meta.aggregate_status is AggregateStatus.ok
    assert "forced past: unpreserved-history" in (forced.response.meta.message or "")
    assert not dest_b.exists()
    listed = asyncio.run(client.local_family(LocalFamilyOp.list))
    assert [entry.name for entry in listed.members] == ["root"]
    assert git(member, "rev-parse", "HEAD") == _base, "the root is untouched"
