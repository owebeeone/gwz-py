from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from gwz import cli as cli_module
from gwz.errors import GwzBridgeError, GwzOperationError

from gwz.protocol.generated import (
    AggregateStatus,
    BranchActionResult,
    EventKind,
    MergeOperationState,
    MergeParticipantState,
    StashPushLifecycle,
    StashRestoreState,
)

from native_helpers import commit_file, create_workspace_with_member, git, native_client


def test_native_branch_create_list_and_direct_merge_deprecation(tmp_path: Path) -> None:
    repo, _ = create_workspace_with_member(tmp_path)
    client = native_client(tmp_path)

    created = asyncio.run(client.branch("feature/native", op="create", paths=["repos/app"]))
    listed = asyncio.run(client.branch(op="list", paths=["repos/app"]))

    assert created.response.meta.aggregate_status is AggregateStatus.ok
    assert created.repos is not None
    assert created.repos[0].result is BranchActionResult.created
    assert listed.repos is not None
    assert any(repo_summary.branch == "feature/native" for repo_summary in listed.repos)

    asyncio.run(client.branch("feature/source", op="create", paths=["repos/app"]))
    git(repo, "checkout", "feature/source")
    commit_file(repo, "source.txt", "source\n", "source")
    git(repo, "checkout", "main")
    commit_file(repo, "local.txt", "local\n", "local")

    with pytest.raises(GwzBridgeError, match="DeprecatedOperation.*first-class merge"):
        asyncio.run(client.branch(op="merge", source_ref="feature/source", paths=["repos/app"]))

    before = git(repo, "rev-parse", "HEAD")
    planned = asyncio.run(client.merge("feature/source", dry_run=True, paths=["repos/app"]))
    assert planned.repos[0].state is MergeParticipantState.planned
    assert git(repo, "rev-parse", "HEAD") == before

    merged = asyncio.run(client.merge("feature/source", paths=["repos/app"]))
    assert merged.response.meta.aggregate_status is AggregateStatus.ok
    assert merged.state is MergeOperationState.completed
    assert merged.open is False
    assert merged.repos[0].state is MergeParticipantState.merged


def test_native_first_class_merge_dispatch_reaches_core_validation(tmp_path: Path) -> None:
    create_workspace_with_member(tmp_path)
    client = native_client(tmp_path)

    with pytest.raises(GwzBridgeError, match="MergeValidationFailed.*source_ref"):
        asyncio.run(client.merge("", paths=["repos/app"]))


def test_native_gate_uses_explicit_root_outside_cwd_and_stage_is_conditional(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo, _ = create_workspace_with_member(tmp_path)
    client = native_client(tmp_path)
    git(repo, "checkout", "-b", "feature/source")
    commit_file(repo, "README.md", "source\n", "source")
    git(repo, "checkout", "main")
    commit_file(repo, "README.md", "target\n", "target")
    with pytest.raises(GwzOperationError) as conflict:
        asyncio.run(client.merge("feature/source", paths=["repos/app"]))
    started = conflict.value.response
    assert started.state is MergeOperationState.awaiting_resolution

    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    monkeypatch.chdir(outside)

    for dry_run in [True, False]:
        request_id = f"req_client_open_{dry_run}"
        with pytest.raises(GwzBridgeError, match="OpenOperation"):
            asyncio.run(
                client.merge(
                    "feature/source",
                    dry_run=dry_run,
                    paths=["repos/app"],
                    request_id=request_id,
                )
            )
        events = asyncio.run(collect_events(client.events(f"op_{request_id}")))
        assert [event.kind for event in events] == [
            EventKind.operation_started,
            EventKind.operation_finished,
        ]

    for machine_flag in ["--json", "--jsonl"]:
        assert (
            cli_module.main(
                [
                    "--root",
                    str(tmp_path),
                    machine_flag,
                    "merge",
                    "feature/source",
                ]
            )
            == 1
        )
        records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        if machine_flag == "--jsonl":
            assert [
                record["event_kind"]
                for record in records
                if record.get("kind") == "event"
            ] == ["OperationStarted", "OperationFinished"]
        assert records[-1]["errors"][0]["code"] == "OpenOperation"

    with pytest.raises(GwzBridgeError, match="OpenOperation"):
        asyncio.run(
            client.branch(
                "must-not-exist",
                op="create",
                start_ref="HEAD",
                paths=["repos/app"],
            )
        )
    assert "must-not-exist" not in git(repo, "branch", "--format=%(refname:short)").splitlines()

    (repo / "README.md").write_text("resolved\n", encoding="utf-8")
    staged = asyncio.run(client.stage(["repos/app/README.md"], cwd=tmp_path))
    assert staged.response.meta.aggregate_status is AggregateStatus.ok
    assert "README.md" in git(repo, "diff", "--cached", "--name-only")

    (tmp_path / "root-new.txt").write_text("blocked\n", encoding="utf-8")
    with pytest.raises(GwzBridgeError, match="OpenOperation"):
        asyncio.run(client.stage(["root-new.txt"], cwd=tmp_path))
    assert "root-new.txt" not in git(tmp_path, "diff", "--cached", "--name-only")


async def collect_events(events):
    return [event async for event in events]


@pytest.mark.parametrize(
    ("machine_flag", "command"),
    [
        (None, ["branch", "--merge"]),
        ("--json", ["merge"]),
        ("--jsonl", ["merge"]),
    ],
)
def test_native_cli_merge_conflict_preserves_structured_response(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    machine_flag: str | None,
    command: list[str],
) -> None:
    repo, _ = create_workspace_with_member(tmp_path)
    git(repo, "checkout", "-b", "feature/source")
    commit_file(repo, "README.md", "source\n", "source")
    git(repo, "checkout", "main")
    commit_file(repo, "README.md", "target\n", "target")

    argv = ["--root", str(tmp_path)]
    if machine_flag is not None:
        argv.append(machine_flag)
    assert cli_module.main([*argv, *command, "feature/source"]) == 1
    output = capsys.readouterr().out

    if machine_flag is None:
        assert "repos/app (mem_app)  conflicted" in output
        assert "source: feature/source @" in output
        assert "README.md" in output
        return
    records = [json.loads(line) for line in output.splitlines()]
    if machine_flag == "--jsonl":
        assert records[0]["kind"] == "event"
        assert records[-1]["kind"] == "response"
        assert all(record["kind"] == "event" for record in records[:-1])
    else:
        assert len(records) == 1
    payload = records[-1]
    assert payload["meta"]["aggregate_status"] == "Conflicted"
    assert payload["merge"]["state"] == "AwaitingResolution"
    assert payload["merge"]["repos"][0]["state"] == "Conflicted"
    assert payload["merge"]["repos"][0]["conflict_paths"] == ["README.md"]


def test_native_stash_push_list_apply_pop_and_drop(tmp_path: Path) -> None:
    repo, _ = create_workspace_with_member(tmp_path)
    client = native_client(tmp_path)
    readme = repo / "README.md"

    readme.write_text("apply change\n", encoding="utf-8")
    pushed = asyncio.run(
        client.stash(op="push", stash_id="stash_apply", message="apply", paths=["repos/app"])
    )
    listed = asyncio.run(client.stash(op="list", expanded=True, paths=["repos/app"]))

    assert pushed.response.meta.aggregate_status is AggregateStatus.ok
    assert pushed.bundles is not None
    pushed_member = pushed.bundles[0].members[0]
    assert pushed_member.push_lifecycle is StashPushLifecycle.saved
    assert pushed_member.restore_state is StashRestoreState.pending
    assert readme.read_text(encoding="utf-8") == "one\n"
    assert listed.bundles is not None
    assert listed.bundles[0].stash_id == "stash_apply"

    applied = asyncio.run(client.stash(op="apply", stash_id="stash_apply", paths=["repos/app"]))

    assert readme.read_text(encoding="utf-8") == "apply change\n"
    assert applied.bundles is not None
    assert applied.bundles[0].members[0].restore_state is StashRestoreState.applied
    git(repo, "reset", "--hard", "HEAD")

    readme.write_text("pop change\n", encoding="utf-8")
    asyncio.run(client.stash(op="push", stash_id="stash_pop", message="pop", paths=["repos/app"]))
    popped = asyncio.run(client.stash(op="pop", stash_id="stash_pop", paths=["repos/app"]))

    assert readme.read_text(encoding="utf-8") == "pop change\n"
    assert popped.bundles is not None
    assert popped.bundles[0].members[0].restore_state is StashRestoreState.popped
    git(repo, "reset", "--hard", "HEAD")

    readme.write_text("drop change\n", encoding="utf-8")
    asyncio.run(client.stash(op="push", stash_id="stash_drop", message="drop", paths=["repos/app"]))
    dropped = asyncio.run(client.stash(op="drop", stash_id="stash_drop", paths=["repos/app"]))
    listed_after_drop = asyncio.run(client.stash(op="list", expanded=True, paths=["repos/app"]))

    assert dropped.response.meta.aggregate_status is AggregateStatus.ok
    assert dropped.bundles is not None
    assert dropped.bundles[0].members[0].restore_state is StashRestoreState.dropped
    assert listed_after_drop.bundles is not None
    assert all(bundle.stash_id != "stash_drop" for bundle in listed_after_drop.bundles)
