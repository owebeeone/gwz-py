"""A `failed` or `rejected` result, like a `partial` one, lists each failed or
refused member's error in its top-level `errors`, and
`GwzOperationError.member_errors` is that list (TR2.3, widened by TR2.19;
`gwz-cli/docs/MachineOutput.md`, "Failed, rejected and partial results"). The
human reports print no copy. The partial results' rows are in
`test_push_end_to_end.py`, whose published workspace these tests share.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from gwz.cli_render import render_response
from gwz.errors import GwzOperationError
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    GwzError,
    GwzErrorCode,
    MemberResponse,
    MemberStatus,
    ResponseEnvelope,
    ResponseMeta,
    SourceKind,
    StatusResponse,
    TargetKind,
    WorkspaceGitStatus,
)

from native_helpers import native_client
from test_push_end_to_end import PYTHON_CLI, PublishedWorkspace, error_facts, run_cli, rust_cli


def test_a_failed_fetch_lists_each_failed_repositorys_error_in_errors(tmp_path: Path) -> None:
    workspace = PublishedWorkspace(tmp_path)
    # Every remote moves away, so nothing answers.
    for name in ("app", "lib", "root"):
        workspace.remote(name).rename(workspace.remotes / f"moved-{name}.git")

    machine = run_cli(PYTHON_CLI, workspace.root, "--json", "fetch")
    with pytest.raises(GwzOperationError) as raised:
        asyncio.run(native_client(workspace.root).fetch())

    envelope = raised.value.response.response
    assert envelope.meta.aggregate_status is AggregateStatus.failed
    assert [(row.member_id, row.status) for row in envelope.members] == [
        ("@root", MemberStatus.failed), ("mem_app", MemberStatus.failed), ("mem_lib", MemberStatus.failed),
    ]
    copies = [row.error for row in envelope.members]
    assert envelope.errors == copies
    assert raised.value.member_errors == copies
    # The CLI's error document lists each failure rather than only the status.
    assert machine.returncode == 1, (machine.stdout, machine.stderr)
    assert [fact[2] for fact in error_facts(json.loads(machine.stdout))] == ["@root", "mem_app", "mem_lib"]


def test_a_rejected_push_lists_each_refused_repositorys_error_in_errors(tmp_path: Path) -> None:
    workspace = PublishedWorkspace(tmp_path)

    # No repository has a remote named `nope`: each is refused before any remote
    # is contacted.
    with pytest.raises(GwzOperationError) as raised:
        asyncio.run(native_client(workspace.root).push(remote="nope"))

    envelope = raised.value.response.response
    assert envelope.meta.aggregate_status is AggregateStatus.rejected
    assert [(row.member_id, row.status) for row in envelope.members] == [
        ("mem_app", MemberStatus.rejected), ("mem_lib", MemberStatus.rejected), ("@root", MemberStatus.rejected),
    ]
    copies = [row.error for row in envelope.members]
    assert envelope.errors == copies
    assert raised.value.member_errors == copies


def test_both_clis_list_a_rejected_pushs_refusals_in_errors(tmp_path: Path) -> None:
    workspace = PublishedWorkspace(tmp_path)

    facts = {}
    for name, driver in (("rust", rust_cli()), ("python", PYTHON_CLI)):
        result = run_cli(driver, workspace.root, "--json", "--remote", "nope", "push")
        assert result.returncode == 2, (name, result.stdout, result.stderr)
        document = json.loads(result.stdout)
        # The Rust CLI's document is the envelope; gwz-py nests it under `response`.
        facts[name] = error_facts(document.get("response", document))

    assert facts["rust"] == facts["python"]
    assert [fact[2] for fact in facts["rust"]] == ["mem_app", "mem_lib", "@root"]


def test_a_failed_status_report_lists_each_issue_once() -> None:
    """The status report lists a member's failure under "Issues:" from its row,
    and lists no copy of it a second time, as the Rust CLI's report does."""

    def member(member_id: str, status: MemberStatus, message: str) -> MemberResponse:
        error = GwzError(
            code=GwzErrorCode.git_command_failed, message=message, detail=None, member_id=member_id,
            member_path=member_id[4:], target_kind=TargetKind.member, record_context=None,
        )
        return MemberResponse(
            member_id=member_id, member_path=member_id[4:], source_kind=SourceKind.git, status=status,
            error=error, planned=None, state=None, git_status=None, lock_match=None,
            target_kind=TargetKind.member, lock_difference_reasons=None, url_resolution=None,
        )

    members = [
        member("mem_app", MemberStatus.failed, "cannot read HEAD"),
        member("mem_lib", MemberStatus.rejected, "status supports git members only"),
    ]

    def status(errors: list[GwzError]) -> StatusResponse:
        meta = ResponseMeta(
            request_id="req_status", schema_version="gwz.protocol/v0", action=ActionKind.status,
            aggregate_status=AggregateStatus.failed, operation_id="op_status", message=None,
            attribution=None, transport=None,
        )
        workspace = WorkspaceGitStatus(
            clean=False, file_changes=[], branches=[], branch_groups=[], branch_differences=[],
            root_status=None, root_file_changes=[],
        )
        envelope = ResponseEnvelope(meta=meta, members=members, errors=errors)
        return StatusResponse(response=envelope, workspace_git_status=workspace)

    rendered = render_response(status([row.error for row in members]))

    assert rendered == render_response(status([]))
    assert rendered.count("cannot read HEAD") == 1, rendered
    assert rendered.count("git members only") == 1, rendered
