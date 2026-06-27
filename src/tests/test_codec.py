from __future__ import annotations

import pytest
from taut.wire import codec as wire_codec

from gwz.errors import GwzProtocolError
from gwz.protocol import generated
from gwz.protocol.codec import (
    decode_message,
    encode_message,
    event_message_name,
    from_wire,
    generated_classes,
    ir_fingerprint,
    protocol_metadata,
    request_message_name,
    request_message_names,
    response_message_name,
    response_message_names,
    result_message_name,
    schema,
    to_wire,
)


def _request_meta(request_id: str = "req_test") -> generated.RequestMeta:
    return generated.RequestMeta(
        request_id=request_id,
        schema_version="gwz.protocol/v0",
        workspace=generated.WorkspaceRef(root="/workspace", workspace_id=None),
        selection=generated.Selection(
            all=None,
            member_ids=["app", "lib"],
            paths=["packages/app"],
            targets=["@root"],
            exclude_targets=["@default"],
        ),
        policy=generated.OperationPolicy(
            partial=generated.PartialBehavior.partial,
            destructive=generated.DestructiveBehavior.allow,
            sync=generated.SyncBehavior.ff_only,
            unsupported_member=generated.UnsupportedMemberBehavior.skip,
            remote="origin",
            concurrency=4,
            progress_min_interval_ms=None,
            max_connections_per_host=8,
        ),
        dry_run=True,
        attribution=generated.OperationAttribution(
            actor=generated.OperationActor(
                actor_id="tester",
                display_name="Test User",
                email=None,
                authority="unit-test",
            ),
            git_author=generated.GitObjectIdentity(
                name="Test User",
                email="test@example.com",
                time_ms=1_700_000_000_000,
                timezone_offset_minutes=600,
            ),
            git_committer=None,
            credential_ref=None,
        ),
    )


def _response_envelope(action: generated.ActionKind) -> generated.ResponseEnvelope:
    return generated.ResponseEnvelope(
        meta=generated.ResponseMeta(
            request_id="req_test",
            schema_version="gwz.protocol/v0",
            action=action,
            aggregate_status=generated.AggregateStatus.ok,
            operation_id="op_test",
            message=None,
            attribution=None,
        ),
        members=[],
        errors=[],
    )


def _assert_cbor_round_trip(message_name: str, value: object) -> None:
    payload = to_wire(value)
    encoded = wire_codec.encode(schema(), message_name, payload)
    decoded_payload = wire_codec.decode(schema(), message_name, encoded)

    assert from_wire(message_name, decoded_payload) == value
    assert decode_message(message_name, encode_message(message_name, value)) == value


def test_generated_registry_service_lookup_and_metadata() -> None:
    classes = generated_classes()

    assert classes["StatusRequest"] is generated.StatusRequest
    assert classes["ListSnapshotsResponse"] is generated.ListSnapshotsResponse
    assert classes["BranchResponse"] is generated.BranchResponse
    assert classes["StashResponse"] is generated.StashResponse
    assert request_message_name("status") == "StatusRequest"
    assert request_message_name("list_snapshots") == "ListSnapshotsRequest"
    assert response_message_name("status") == "StatusResponse"
    assert response_message_name("list_snapshots") == "ListSnapshotsResponse"
    assert request_message_names()["branch"] == "BranchRequest"
    assert response_message_names()["stash"] == "StashResponse"
    assert event_message_name() == "OperationEvent"
    assert result_message_name() == "OperationResult"

    metadata = protocol_metadata()
    assert metadata["schema_version"]["status"] == "unresolved"
    assert metadata["schema_version"]["value"] is None
    assert ir_fingerprint().startswith("sha256:")


def test_status_request_round_trip_covers_nested_enums_optional_values_and_lists() -> None:
    request = generated.StatusRequest(
        meta=_request_meta("req_status"),
        mode=generated.StatusMode.combined,
        include_file_changes=True,
        include_branch_summary=None,
        path_style=generated.StatusPathStyle.workspace_relative,
    )

    _assert_cbor_round_trip("StatusRequest", request)


def test_nested_dataclass_list_round_trip() -> None:
    response = generated.LsResponse(
        response=_response_envelope(generated.ActionKind.ls),
        members=[
            generated.MemberEntry(
                id="app",
                path="packages/app",
                abspath="/workspace/packages/app",
                materialized=True,
                target_kind=None,
            )
        ],
    )

    _assert_cbor_round_trip("LsResponse", response)


def test_branch_generated_response_round_trip() -> None:
    response = generated.BranchResponse(
        response=_response_envelope(generated.ActionKind.branch),
        repos=[
            generated.BranchRepoSummary(
                member_id="app",
                member_path="packages/app",
                source_kind=generated.SourceKind.git,
                result=generated.BranchActionResult.created,
                branch="feature/test",
                current_branch="main",
                detached=False,
                unborn=False,
                head="abc123",
                upstream=None,
                ahead=0,
                behind=0,
                source_ref="HEAD",
                target_branch="feature/test",
                resulting_commit="abc123",
                conflict_paths=[],
            )
        ],
    )

    _assert_cbor_round_trip("BranchResponse", response)


def test_stash_generated_response_round_trip() -> None:
    response = generated.StashResponse(
        response=_response_envelope(generated.ActionKind.stash),
        bundles=[
            generated.StashBundle(
                schema="gwz.stash/v0",
                workspace_id="ws_test",
                stash_id="stash_test",
                created_at="2026-01-01T00:00:00Z",
                message_suffix="work in progress",
                include_untracked=True,
                include_ignored=False,
                members=[
                    generated.StashBundleMember(
                        member_id="app",
                        path="packages/app",
                        participation=generated.StashParticipation.stashed,
                        push_lifecycle=generated.StashPushLifecycle.saved,
                        restore_state=generated.StashRestoreState.pending,
                        branch_before="main",
                        head_before="abc123",
                        full_stash_message="GWZ stash: work in progress",
                        dirty_summary=generated.StashDirtySummary(
                            staged=True,
                            unstaged=True,
                            untracked=False,
                            ignored=False,
                        ),
                        native_stash_object_id="stash-object",
                        native_stash_display_ref="stash@{0}",
                        error=None,
                    )
                ],
                warnings=[
                    generated.StashWarning(
                        code="note",
                        message="stored",
                        member_id=None,
                    )
                ],
                drift=[
                    generated.StashDrift(
                        code="branch",
                        message="branch changed",
                        member_id="app",
                    )
                ],
                selected_members=["app"],
            )
        ],
    )

    _assert_cbor_round_trip("StashResponse", response)


def test_unsupported_message_name_raises_protocol_error() -> None:
    with pytest.raises(GwzProtocolError, match="unsupported message"):
        from_wire("NoSuchMessage", {})

    with pytest.raises(GwzProtocolError, match="unsupported message"):
        encode_message("NoSuchMessage", object())

    with pytest.raises(GwzProtocolError, match="unsupported message"):
        decode_message("NoSuchMessage", b"")
