"""The local clone family through the native bridge (LCM1.2, lane C).

`test_cli_local_family.py` pins what the Python driver *sends* against the
cross-driver parity fixture; this file runs the served verbs against real
core: a verbatim `clone --local`, work committed in the clone, and
`merge --remote A` integrating it through the retained import ref -- the
same loop `gwz-cli/tests/local_family_workflows.rs` drives through the Rust
binary, so the two drivers are compared on the same outcome.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from gwz.protocol.generated import (
    AggregateStatus,
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
    merged = asyncio.run(client.merge(local_source_name="A"))
    assert merged.response.meta.aggregate_status is AggregateStatus.ok
    assert merged.state is MergeOperationState.completed
    assert merged.open is False
    [repo] = merged.repos
    assert repo.target_id == "mem_app"
    assert repo.state is MergeParticipantState.fast_forwarded
    assert repo.source_ref.startswith("refs/gwz/local-imports/xfer_")
    assert repo.source_commit == work
    assert repo.resulting_commit == work
    message = merged.response.meta.message or ""
    assert f"imported HEAD of family member `A` as {repo.source_ref} (mem_app={work})" in message
    assert git(member, "rev-parse", "HEAD") == work
    assert git(member, "rev-parse", repo.source_ref) == work, "the import ref is retained"
    assert git(member, "remote") == "", "no family remote is persisted"

    # A retry after more work mints a fresh transfer id; the earlier ref stays.
    more = commit_file(clone_member, "feature.txt", "more from A\n", "more work in A")
    again = asyncio.run(client.merge(op=MergeOp.start, local_source_name="A"))
    assert again.state is MergeOperationState.completed
    [repo_again] = again.repos
    assert repo_again.source_ref != repo.source_ref
    assert git(member, "rev-parse", "HEAD") == more
    assert git(member, "rev-parse", repo.source_ref) == work
    assert git(member, "rev-parse", repo_again.source_ref) == more
