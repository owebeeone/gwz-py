from __future__ import annotations

import dataclasses

import pytest
from taut.ir.model import Schema
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
            max_retries=None,
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
        transport=None,
        invocation=None,
        transport_message=None,
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
                transport=None,
                transport_message=None,
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
    assert classes["MergeResponse"] is generated.MergeResponse
    assert classes["StashResponse"] is generated.StashResponse
    assert request_message_name("status") == "StatusRequest"
    assert request_message_name("list_snapshots") == "ListSnapshotsRequest"
    assert response_message_name("status") == "StatusResponse"
    assert response_message_name("list_snapshots") == "ListSnapshotsResponse"
    assert request_message_names()["branch"] == "BranchRequest"
    assert request_message_names()["merge"] == "MergeRequest"
    assert response_message_names()["stash"] == "StashResponse"
    assert response_message_names()["merge"] == "MergeResponse"
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
                # Tag 6 (GwzOpenDecisions D3). Runtime-required field only:
                # the generated dataclass has no default, and an ordinary
                # row -- one the lock records and the filesystem has --
                # carries no note.
                note=None,
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


def test_merge_reserved_lifecycle_shape_round_trip() -> None:
    request = generated.MergeRequest(
        meta=_request_meta("req_merge"),
        op=generated.MergeOp.start,
        source_ref="feature/protocol",
        merge_id=None,
        mode=generated.MergeMode.normal,
        message=None,
        preserve=None,
        filesystem_strict=None,
        local_source_name=None,
        wait_seconds=None,
    )
    _assert_cbor_round_trip("MergeRequest", request)

    parity_request = generated.MergeRequest(
        meta=generated.RequestMeta(
            request_id="req_merge",
            schema_version="gwz.v0",
            workspace=None,
            selection=None,
            policy=None,
            dry_run=None,
                attribution=None,
                transport=None,
                invocation=None,
                transport_message=None,
        ),
        op=generated.MergeOp.start,
        source_ref="feature/x",
        merge_id=None,
        mode=generated.MergeMode.normal,
        message=None,
        preserve=None,
        filesystem_strict=None,
        local_source_name=None,
        wait_seconds=None,
    )
    # Moved 2026-09-03 by DR-1 ship (1) W1
    # (gwz-dev dev-docs/GwzM5-8DR1-WarnOrRefuse-Charter.md §3.7): MergeRequest
    # gained the optional `filesystem_strict` in slot 8, so the map header grows
    # from a7 to a8 and a trailing `08 f6` (slot 8 = null) is appended. Every
    # pre-existing slot stays byte-identical, and this hex stays byte-identical
    # to gwz-core's own parity pin in tests/protocol.rs.
    #   was: "a701a701697265715f6d65726765026667777a2e763003f604f605f606f607f6"
    #        "02000369666561747572652f7804f6050006f607f6"
    # Moved 2026-09-05 by LCM1.0c (gwz-dev dev-docs/GwzLocalCloneDesign.md §7):
    # MergeRequest gained the optional `local_source_name` in slot 9, so the
    # map header grows from a8 to a9 and a trailing `09 f6` (slot 9 = null) is
    # appended. Every pre-existing slot stays byte-identical, and this hex
    # stays byte-identical to gwz-core's own parity pin in tests/protocol.rs.
    #   was: "a801a701697265715f6d65726765026667777a2e763003f604f605f606f607f6"
    #        "02000369666561747572652f7804f6050006f607f608f6"
    # InvocationContext occupies RequestMeta tag 9.  The null compatibility
    # value leaves every earlier field byte-identical.
    #
    # Moved 2026-09-18 by GwzOpenDecisions D1, which carries
    # GwzLaneCleanFixes R21's `--wait <secs>` to `gwz merge --remote <name>`
    # and adds the optional `wait_seconds` at tag 10. MEASURED additive, and
    # measured on this driver's own encoder: the map header grows a9 -> aa
    # and the encoding gains exactly the trailing `0a f6` (slot 10 = null);
    # every earlier slot is byte-identical, and the result is byte-identical
    # to gwz-core's twin pin in tests/protocol.rs, which is the whole point
    # of having two.
    #   was: "a901a901697265715f6d65726765026667777a2e763003f604f605f606f607f6"
    #        "08f609f602000369666561747572652f7804f6050006f607f608f609f6"
    #
    # Moved by plan step 2.2: RequestMeta gained the optional `transport_message`
    # at tag 10 (MISSING_OK), so the nested map header grows a9 -> aa and gains
    # exactly the trailing `0a f6`; byte-identical to gwz-core's twin pin in
    # tests/protocol.rs.
    #   was: "aa01a901697265715f6d65726765026667777a2e763003f604f605f606f607f608f609f6"
    #        "02000369666561747572652f7804f6050006f607f608f609f60af6"
    assert encode_message("MergeRequest", parity_request).hex() == (
        "aa01aa01697265715f6d65726765026667777a2e763003f604f605f606f607f608f609f60af6"
        "02000369666561747572652f7804f6050006f607f608f609f60af6"
    )
    # LCM1.0c follow-up 2 (operator rulings 2026-09-05, gwz-dev
    # dev-docs/GwzLocalCloneDesign.md revision 9 §7, §11 item 12): the `gwz
    # local list` payload row. Seven slots since R20, the three enums by the
    # wire values pinned in test_protocol.py (checkout=0, ready=1,
    # pointer_removed=4), `last_error` and `owner` null. Byte-identical to
    # gwz-core's parity pin in tests/protocol.rs
    # (`local_clone_follow_up_2_allocations_are_pinned`). The MergeRequest pin
    # above is UNMOVED by follow-up 2: no MergeRequest slot changed.
    #
    # Moved on 2026-09-17 by GwzLaneCleanFixes R20 (gwz-core
    # dev-docs/GwzLaneCleanFixes.md §3.6), which adds the optional `owner` at
    # tag 7. MEASURED additive, and measured on this driver's own encoder: the
    # map header grows a6 -> a7 and the encoding gains exactly the trailing
    # `07 f6`, leaving every earlier slot byte-identical. The MergeRequest pin
    # above is UNMOVED by R20: no MergeRequest slot changed.
    #   was: "a601614102000301040405672e2e2f77732d4106f6"
    entry = generated.LocalFamilyMemberEntry(
        name="A",
        kind=generated.LocalMemberKind.checkout,
        recorded_state=generated.LocalMemberState.ready,
        observed_state=generated.LocalObservedState.pointer_removed,
        path="../ws-A",
        last_error=None,
        owner=None,
    )
    assert encode_message("LocalFamilyMemberEntry", entry).hex() == (
        "a701614102000301040405672e2e2f77732d4106f607f6"
    )
    _assert_cbor_round_trip("LocalFamilyMemberEntry", entry)

    response = generated.MergeResponse(
        response=_response_envelope(generated.ActionKind.merge),
        merge_id="merge_1",
        state=generated.MergeOperationState.awaiting_resolution,
        open=True,
        participant_counts=generated.MergeParticipantCounts(
            total=1,
            planned=0,
            up_to_date=0,
            fast_forwarded=0,
            merged=0,
            conflicted=1,
            failed=0,
            unattempted=0,
            continued=0,
            aborted=0,
            rolled_back=0,
        ),
        repos=[
            generated.MergeRepoSummary(
                target_id="mem_core",
                target_kind=generated.TargetKind.member,
                path="repos/core",
                source_ref="feature/protocol",
                source_commit="222",
                target_branch="main",
                before_commit="111",
                resulting_commit=None,
                live_commit="111",
                state=generated.MergeParticipantState.conflicted,
                predicted=generated.MergeAnalysisKind.true_merge,
                prediction_complete=False,
                conflict_paths=["src/lib.rs"],
                continue_eligible=False,
                abort_eligible=True,
                drift=[],
                error=None,
                pending_action=None,
            )
        ],
        operation_drift=[],
        preservation=[],
        publication_step=None,
        record=None,
        crash_recovery=None,
    )
    _assert_cbor_round_trip("MergeResponse", response)


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


# S7.1 (1.1.0) step C2.2: the five members core's ordinary protocol gained.
# These bytes were encoded by the pre-C2 schema (no tags 9 to 12); the new
# reader accepts them and reads the new members as absent.
PRE_C2_POLICY = bytes.fromhex("a8010102f603f604f605666f726967696e060407f60808")
PRE_C2_OBSERVATION = bytes.fromhex(
    "a8016a7265706f732f636f726502666f726967696e03010402050006f507f508f6"
)


def _observation(**receipts: object) -> generated.TransportObservation:
    values: dict[str, object] = {
        "endpoint_id": None,
        "connection_id": None,
        "stream_id": None,
        "reused": None,
    }
    values.update(receipts)
    return generated.TransportObservation(
        repository_path="repos/core",
        remote="origin",
        operation=generated.TransportOperation.fetch,
        credential_method=generated.TransportCredentialMethod.agent,
        selection_source=generated.TransportSelectionSource.ambient,
        credential_offered=True,
        authenticated=True,
        public_key_fingerprint=None,
        **values,
    )


def _policy(**extra: object) -> generated.OperationPolicy:
    return generated.OperationPolicy(
        partial=generated.PartialBehavior.partial,
        destructive=None,
        sync=None,
        unsupported_member=None,
        remote="origin",
        concurrency=4,
        progress_min_interval_ms=None,
        max_connections_per_host=8,
        **extra,
    )


def test_operation_policy_and_observation_round_trip_the_1_1_0_fields() -> None:
    for retries in (0, 3):
        _assert_cbor_round_trip("OperationPolicy", _policy(max_retries=retries))
    _assert_cbor_round_trip("OperationPolicy", _policy(max_retries=None))
    _assert_cbor_round_trip(
        "TransportObservation",
        _observation(
            endpoint_id="endpoint-1", connection_id="connection-1", stream_id=7, reused=True
        ),
    )
    _assert_cbor_round_trip("TransportObservation", _observation(reused=False))
    _assert_cbor_round_trip("TransportObservation", _observation())


def test_pre_c2_bytes_decode_with_the_new_members_absent() -> None:
    policy = decode_message("OperationPolicy", PRE_C2_POLICY)
    assert policy == _policy(max_retries=None)
    assert policy.concurrency == 4
    observation = decode_message("TransportObservation", PRE_C2_OBSERVATION)
    assert observation == _observation()
    assert observation.remote == "origin"


def test_the_new_members_are_written_at_tags_nine_to_twelve() -> None:
    policy = wire_codec.encode_struct(schema(), "OperationPolicy", to_wire(_policy(max_retries=0)))
    assert policy[9] == 0
    observation = wire_codec.encode_struct(
        schema(),
        "TransportObservation",
        to_wire(_observation(endpoint_id="e", connection_id="c", stream_id=7, reused=True)),
    )
    assert (observation[9], observation[10], observation[11], observation[12]) == ("e", "c", 7, True)


def test_unsupported_message_name_raises_protocol_error() -> None:
    with pytest.raises(GwzProtocolError, match="unsupported message"):
        from_wire("NoSuchMessage", {})

    with pytest.raises(GwzProtocolError, match="unsupported message"):
        encode_message("NoSuchMessage", object())

    with pytest.raises(GwzProtocolError, match="unsupported message"):
        decode_message("NoSuchMessage", b"")


# The additive-decoding gate (placement design section 3), the Python half; gwz-core's
# tests/protocol.rs has the Rust half. Plan step 2.2 added the `cli` placement and the
# embedded message to the ordinary schema as MISSING_OK slots, so a writer that predates
# them, or that wrote null, must still decode, and a present malformed value must not.
_PLACEMENT_MEMBERS = {
    "TransportOptions": ("placement", "endpoint_path_base"),
    "RequestMeta": ("transport_message",),
    "ResponseMeta": ("transport_message",),
    "TransportCapabilitiesResponse": (
        "message_versions",
        "placements",
        "schemes",
        "auth_policies",
        "message_limits",
        # Step 2.2b's MISSING_OK cancellation (tag 8): an older writer predates it too.
        "cancellation",
    ),
}


def _schema_without_the_placement_members() -> Schema:
    """The schema as an older writer had it: step 2.2's members removed, nothing else."""
    current = schema()
    messages = dict(current.messages)
    for message_name, members in _PLACEMENT_MEMBERS.items():
        message = messages[message_name]
        kept = tuple(field for field in message.fields if field.name not in members)
        assert len(kept) == len(message.fields) - len(members), message_name
        messages[message_name] = dataclasses.replace(message, fields=kept)
    return Schema(
        enums=dict(current.enums),
        messages=messages,
        services=dict(current.services),
        extensions=current.extensions,
    )


def _older_writers_bytes(message_name: str, value: object) -> bytes:
    older = _schema_without_the_placement_members()
    members = _PLACEMENT_MEMBERS.get(message_name, ())
    payload = {key: item for key, item in to_wire(value).items() if key not in members}
    return wire_codec.encode(older, message_name, payload)


def _envelope() -> generated.Envelope:
    return generated.Envelope(
        version=2,
        session_id="session-1",
        stream_id=0,
        kind=generated.MessageKind.close,
        message_seq=None,
        bind=None,
        bound=None,
        bind_rejected=None,
        open=None,
        opened=None,
        open_failed=None,
        data=None,
        window=None,
        flush=None,
        flushed=None,
        end_write=None,
        close=generated.Close(final_offset=0),
        closed=None,
        cancel=None,
        failed=None,
        check_identity=None,
        identity_checked=None,
        identity_check_failed=None,
    )


def test_placement_and_attachment_fields_round_trip_and_decode_when_the_writer_lacks_them() -> None:
    options = generated.TransportOptions(
        default_identity=None,
        remote_identities=[],
        url_scheme=None,
        placement=generated.TransportPlacement.cli,
        endpoint_path_base="/home/user/work",
    )
    _assert_cbor_round_trip("TransportOptions", options)
    assert generated.TransportPlacement.local.value == 1
    assert generated.TransportPlacement.cli.value == 2

    request = dataclasses.replace(_request_meta("req_placement"), transport_message=_envelope())
    _assert_cbor_round_trip("RequestMeta", request)
    response_meta = dataclasses.replace(
        _response_envelope(generated.ActionKind.status).meta, transport_message=_envelope()
    )
    _assert_cbor_round_trip("ResponseMeta", response_meta)
    capabilities = generated.TransportCapabilitiesResponse(
        file_identity=True,
        exact_agent_identity=False,
        message_versions=[2],
        placements=[generated.TransportPlacement.local, generated.TransportPlacement.cli],
        schemes=[generated.Scheme.ssh, generated.Scheme.https],
        auth_policies=[generated.AuthPolicy.ssh_ambient, generated.AuthPolicy.gh],
        message_limits=generated.Limits(
            encoded_frame=1,
            data_payload=1,
            metadata_bytes=1,
            nesting=1,
            collection_entries=1,
            decode_allocation=1,
            queued_bytes=1,
            queued_frames=1,
            receive_window=1,
            control_reserve_bytes=1,
            control_reserve_frames=1,
        ),
        cancellation=True,
    )
    _assert_cbor_round_trip("TransportCapabilitiesResponse", capabilities)

    # An older writer's bytes lack the keys: every new member reads as absent.
    older_options = generated.TransportOptions(
        default_identity="/tmp/key",
        remote_identities=[],
        url_scheme=None,
        placement=None,
        endpoint_path_base=None,
    )
    assert decode_message("TransportOptions", _older_writers_bytes("TransportOptions", older_options)) == older_options
    older_request = _request_meta("req_older")
    assert decode_message("RequestMeta", _older_writers_bytes("RequestMeta", older_request)) == dataclasses.replace(
        older_request, transport_message=None
    )
    older_response = _response_envelope(generated.ActionKind.status).meta
    assert decode_message("ResponseMeta", _older_writers_bytes("ResponseMeta", older_response)) == older_response
    older_capabilities = generated.TransportCapabilitiesResponse(
        file_identity=True,
        exact_agent_identity=True,
        message_versions=None,
        placements=None,
        schemes=None,
        auth_policies=None,
        message_limits=None,
        cancellation=None,
    )
    assert (
        decode_message(
            "TransportCapabilitiesResponse",
            _older_writers_bytes("TransportCapabilitiesResponse", older_capabilities),
        )
        == older_capabilities
    )
    # The older bytes are shorter than the current writer's: the keys really are absent,
    # and they are the bytes a writer that predates step 2.2 put on the wire.
    assert _older_writers_bytes("TransportOptions", older_options) == bytes.fromhex(
        "a3" "01" "682f746d702f6b6579" "02" "80" "03" "f6"
    )
    assert _older_writers_bytes("TransportCapabilitiesResponse", older_capabilities) == bytes.fromhex(
        "a2" "01" "f5" "02" "f5"
    )
    assert len(_older_writers_bytes("TransportOptions", older_options)) < len(
        encode_message("TransportOptions", older_options)
    )

    # Null reads the same as absent.
    assert decode_message("TransportOptions", encode_message("TransportOptions", older_options)) == older_options


def test_a_malformed_placement_value_is_an_error_not_an_absent_field() -> None:
    # {1: null, 2: [], 3: null, 4: "cli"}: the placement is text where an enum is wire-encoded.
    text_placement = bytes.fromhex("a401f60280" "03f604" "63636c69")
    with pytest.raises(GwzProtocolError):
        decode_message("TransportOptions", text_placement)
    # {1: null, 2: [], 3: null, 4: 3}: 3 is not a TransportPlacement.
    unknown_placement = bytes.fromhex("a401f60280" "03f60403")
    with pytest.raises(GwzProtocolError):
        decode_message("TransportOptions", unknown_placement)
    # {1: null, 2: [], 3: null, 5: 1}: the path base is text.
    integer_path_base = bytes.fromhex("a401f60280" "03f60501")
    with pytest.raises(GwzProtocolError):
        decode_message("TransportOptions", integer_path_base)
