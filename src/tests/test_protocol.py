from __future__ import annotations

from pathlib import Path

from gwz.protocol import schema
from gwz.protocol.codec import to_wire
from gwz.protocol import generated
from gwz.protocol.generated import RequestMeta, StatusMode, StatusRequest


def test_packaged_ir_contains_gwz_core_service() -> None:
    loaded = schema()
    assert "GwzCore" in loaded.services
    assert "StatusRequest" in loaded.messages
    methods = {method.name for method in loaded.services["GwzCore"].methods}
    assert {
        "repo_sync",
        "clone_repo_member",
        "detach_repo_member",
        "attach_repo_member",
        "branch",
        "merge",
        "stash",
        "list_snapshots",
        "events.subscribe",
        "operation.result",
    } <= methods
    assert "forall" not in methods


def test_repo_member_lifecycle_protocol_is_pinned() -> None:
    assert generated.ActionKind.clone_repo_member.value == 22
    assert generated.ActionKind.detach_repo_member.value == 23
    assert generated.ActionKind.attach_repo_member.value == 24
    assert generated.PlannedAction.detach_member.value == 15
    assert generated.PlannedAction.attach_member.value == 16
    assert generated.GwzErrorCode.source_identity_mismatch.value == 36
    assert generated.ActionKind.merge.value == 25
    assert generated.GwzErrorCode.deprecated_operation.value == 37

    request = generated.CloneRepoMemberRequest(
        meta=generated.RequestMeta(
            request_id="req_clone",
            schema_version="gwz.protocol/v0",
            workspace=None,
            selection=None,
            policy=None,
            dry_run=None,
            attribution=None,
        ),
        source=generated.SourceUrl(
            url="ssh://git.example.test/team/shared.git",
            path="libs/shared",
            remote_name="upstream",
            branch="main",
        ),
        member_id="mem_shared",
        source_id="src_shared",
    )

    assert to_wire(request)["source"] == {
        "url": "ssh://git.example.test/team/shared.git",
        "path": "libs/shared",
        "remote_name": "upstream",
        "branch": "main",
    }
    assert to_wire(generated.DetachRepoMemberRequest(meta=request.meta))["meta"][
        "request_id"
    ] == "req_clone"
    assert to_wire(generated.AttachRepoMemberRequest(meta=request.meta))["meta"][
        "request_id"
    ] == "req_clone"


def test_generated_runtime_artifacts_are_api_only() -> None:
    generated_dir = Path(generated.__file__).parent

    assert {path.name for path in generated_dir.iterdir() if path.is_file()} == {
        "__init__.py",
        "api.py",
        "gwz.ir.json",
    }


def test_generated_dataclasses_convert_to_wire_dicts() -> None:
    request = StatusRequest(
        meta=RequestMeta(
            request_id="req_test",
            schema_version="gwz.protocol/v0",
            workspace=None,
            selection=None,
            policy=None,
            dry_run=None,
            attribution=None,
        ),
        mode=StatusMode.combined,
        include_file_changes=None,
        include_branch_summary=None,
        path_style=None,
    )

    assert to_wire(request)["mode"] == "combined"
    assert to_wire(request)["meta"]["request_id"] == "req_test"
