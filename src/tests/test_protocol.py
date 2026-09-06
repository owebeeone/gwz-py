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
    # LCM1.0c (2026-09-05): the local clone family allocation.
    assert generated.ActionKind.log.value == 26
    assert generated.ActionKind.clone_local_workspace.value == 27
    assert generated.ActionKind.local_family.value == 28
    assert generated.LocalCloneMode.verbatim.value == 0
    assert generated.LocalCloneMode.clean.value == 1
    assert generated.LocalCloneMode.bare.value == 2
    assert generated.LocalFamilyOp.list.value == 0
    assert generated.LocalFamilyOp.dispose.value == 1
    assert generated.LocalFamilyOp.disband.value == 2
    assert generated.GwzErrorCode.deprecated_operation.value == 37
    assert generated.GwzErrorCode.unsupported_record_version.value == 46
    assert generated.GwzErrorCode.terminal_rollback_mismatch.value == 61
    # LCM1.0c follow-up 2 (operator rulings 2026-09-05, gwz-dev
    # dev-docs/GwzLocalCloneDesign.md revision 9 §7, §11 items 11-13): the
    # `--from` wire name, the `gwz local list` payload enums (mirroring
    # gwz_family_model::{MemberKind, MemberState, ListState} in declaration
    # order) and the family-only merge miss.
    assert "copy_source" in generated.CloneLocalWorkspaceRequest.__dataclass_fields__
    assert "members" in generated.LocalFamilyResponse.__dataclass_fields__
    assert generated.LocalMemberKind.checkout.value == 0
    assert generated.LocalMemberKind.bare.value == 1
    assert generated.LocalMemberState.creating.value == 0
    assert generated.LocalMemberState.ready.value == 1
    assert generated.LocalMemberState.disposing.value == 2
    assert [state.value for state in generated.LocalObservedState] == [0, 1, 2, 3, 4, 5, 6, 7]
    assert generated.LocalObservedState.ready.value == 0
    assert generated.LocalObservedState.incomplete.value == 1
    assert generated.LocalObservedState.interrupted_disposal.value == 2
    assert generated.LocalObservedState.missing.value == 3
    assert generated.LocalObservedState.pointer_removed.value == 4
    assert generated.LocalObservedState.mismatched.value == 5
    assert generated.LocalObservedState.malformed.value == 6
    assert generated.LocalObservedState.unobserved.value == 7
    assert generated.GwzErrorCode.unknown_local.value == 62
    # LCM1.1 fix 1 (lane C, 2026-09-06, gwz-dev
    # dev-docs/GwzLocalClone-LCM1.0c-Checkpoint.md §14): the four
    # local-create outcomes that were folded into unsupported_operation and
    # io_error, each distinct from both.
    assert generated.GwzErrorCode.unsupported_source_layout.value == 63
    assert generated.GwzErrorCode.copy_failed.value == 64
    assert generated.GwzErrorCode.source_drift.value == 65
    assert generated.GwzErrorCode.destination_incomplete.value == 66
    # LCM1.2 (lane C, 2026-09-06, gwz-dev
    # dev-docs/GwzLocalClone-LCM1.0c-Checkpoint.md §16): the two family-merge
    # import outcomes, distinct from the codes they would have folded into.
    assert generated.GwzErrorCode.pairing_mismatch.value == 67
    assert generated.GwzErrorCode.import_incomplete.value == 68
    assert generated.GwzErrorCode.member_not_found.value != 67
    assert generated.GwzErrorCode.git_command_failed.value != 68
    # LCM2.1/LCM2.2 (lane C, 2026-09-06, gwz-dev
    # dev-docs/GwzLocalClone-LCM1.0c-Checkpoint.md §17): the three
    # ordinary-disposal outcomes, distinct from the codes they would have
    # folded into.
    assert generated.GwzErrorCode.unwaived_hazard.value == 69
    assert generated.GwzErrorCode.unknown_evidence.value == 70
    assert generated.GwzErrorCode.disposal_incomplete.value == 71
    assert generated.GwzErrorCode.permission_denied.value != 69
    assert generated.GwzErrorCode.unsupported_operation.value != 70
    assert generated.GwzErrorCode.io_error.value != 71
    assert generated.GwzErrorCode.unsupported_operation.value == 14
    assert generated.GwzErrorCode.io_error.value == 28
    assert generated.MergeRecordRequiredWave.a1.value == 0
    assert generated.MergeRecordRequiredWave.a4.value == 3
    pinned = (
        (generated.MergeRecordVersion, [0, 1]),
        (generated.MergeTerminalOutcome, [0, 1]),
        (generated.MergeAcceptanceKind, [0, 1, 2, 3]),
        (generated.MergeInstalledAcceptedWorkspaceKind, [0]),
        (generated.MergeLegacyAcceptanceSource, [0, 1]),
        (generated.MergeLegacyAcceptanceGap, [0, 1, 2, 3]),
        (generated.MergeAcceptedMemberKind, [0, 1, 2]),
        (generated.MergeAcceptedRootKind, [0, 1, 2]),
        (generated.MergeAcceptedMetadataSource, [0, 1]),
        (generated.MergeRecoveryOriginState, list(range(6))),
        (generated.MergeCompatibilityBasePhase, list(range(8))),
        (generated.MergeCompatibilityNextAction, list(range(15))),
    )
    for enum, expected in pinned:
        assert [member.value for member in enum] == expected

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


def test_merge_record_projection_uses_the_frozen_field_names() -> None:
    projection = generated.MergeRecordProjection(
        source_version=generated.MergeRecordVersion.v1,
        archived=False,
        terminal_outcome=None,
        acceptance=None,
        recovery=generated.MergeRecoveryProjection(
            origin_state=generated.MergeRecoveryOriginState.executing,
            base_phase=generated.MergeCompatibilityBasePhase.pre_acceptance,
            next_action=generated.MergeCompatibilityNextAction.report_recovery_required,
            resume_action=generated.MergeCompatibilityNextAction.reconcile_pending_participant,
        ),
    )

    assert to_wire(projection) == {
        "source_version": "v1",
        "archived": False,
        "terminal_outcome": None,
        "acceptance": None,
        "recovery": {
            "origin_state": "executing",
            "base_phase": "pre_acceptance",
            "next_action": "report_recovery_required",
            "resume_action": "reconcile_pending_participant",
        },
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


def test_dr1_crash_recovery_protocol_additions_are_pinned() -> None:
    """DR-1 ship (1) §3.7 (2026-09-03): the warn-or-refuse protocol surface.

    W1 only adds the slots; W3/W4 give them behaviour. The tags are pinned here
    because both drivers and gwz-core must agree on them byte for byte.

    M5d (`GwzM5-8M5d-Charter.md` §3, 2026-09-04) adds exactly one more optional
    field, `handles_ok` (slot 4), with no version bump. The field list stays an
    EXACT pin rather than a subset check: its whole value is that a field
    appearing in gwz-core without a driver following it fails here.
    """
    loaded = schema()

    assert [member.value for member in generated.MergeCrashRecoveryGap] == [0, 1, 2]
    assert generated.MergeCrashRecoveryGap.no_durable_identity.value == 0
    assert generated.MergeCrashRecoveryGap.remote_filesystem.value == 1
    assert generated.MergeCrashRecoveryGap.volatile_filesystem.value == 2
    assert generated.EventKind.diagnostic.value == 8

    assert [(field.name, field.tag, field.optional)
            for field in loaded.messages["MergeCrashRecovery"].fields] == [
        ("supported", 1, False),
        ("filesystem", 2, True),
        ("gap", 3, True),
        ("handles_ok", 4, True),
    ]
    request_tags = {field.name: field.tag for field in loaded.messages["MergeRequest"].fields}
    assert request_tags["preserve"] == 7
    assert request_tags["filesystem_strict"] == 8
    response_tags = {field.name: field.tag for field in loaded.messages["MergeResponse"].fields}
    assert response_tags["record"] == 10
    assert response_tags["crash_recovery"] == 11

    # M5d: `handles_ok` is present only BELOW the bar, where it says whether
    # this volume proves the persistent file handles the checked boundary's
    # reverse doors need. Above the bar there is nothing to plan around and
    # gwz-core leaves it absent.
    decision = generated.MergeCrashRecovery(
        supported=False,
        filesystem="btrfs",
        gap=generated.MergeCrashRecoveryGap.volatile_filesystem,
        handles_ok=True,
    )
    assert to_wire(decision) == {
        "supported": False,
        "filesystem": "btrfs",
        "gap": "volatile_filesystem",
        "handles_ok": True,
    }
    assert to_wire(
        generated.MergeCrashRecovery(
            supported=False,
            filesystem="overlay",
            gap=generated.MergeCrashRecoveryGap.no_durable_identity,
            handles_ok=False,
        )
    ) == {
        "supported": False,
        "filesystem": "overlay",
        "gap": "no_durable_identity",
        "handles_ok": False,
    }
    assert to_wire(
        generated.MergeCrashRecovery(
            supported=True, filesystem="apfs", gap=None, handles_ok=None
        )
    ) == {"supported": True, "filesystem": "apfs", "gap": None, "handles_ok": None}
