"""S2.0 protocol parity for the streamed unified commit log."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from taut.wire import codec as wire_codec

from gwz.protocol import generated
from gwz.protocol.codec import decode_message, encode_message, from_wire, schema, to_wire


# Guards gwz-log against reshaping an OLDER message; additive growth moves it.
# Moved deliberately on 2026-09-03 by DR-1 ship (1) W1 (gwz-dev
# dev-docs/GwzM5-8DR1-WarnOrRefuse-Charter.md §3.7), which adds
# MergeRequest.filesystem_strict (slot 8), MergeResponse.crash_recovery
# (slot 11), MergeCrashRecovery, MergeCrashRecoveryGap and
# EventKind.diagnostic (slot 8). No pre-existing slot changed. Kept identical
# to gwz-core protocol/check_log_additive.py and
# scripts/check_protocol_drift.py.
#   was: d0c205c8767f8d54d32ead2f676a05077d849f6a12278d9de52b3c132c3c9372
#
# Moved deliberately again on 2026-09-04 following gwz-core's M5d step (3)
# (gwz-dev dev-docs/GwzM5-8M5d-Charter.md §3/§10.2), which adds exactly one
# optional field, MergeCrashRecovery.handles_ok (slot 4). No version bump and
# no pre-existing slot changed. The projection strips only `Log*`, so
# MergeCrashRecovery is inside it and this pin moves with the field.
#   was: 7a66e301c5c0147a12c59b2cddb6f2ebc1515ef4d65297ec53c3b312a3769697
#
# ESCAPED DEFECT, repaired 2026-09-05 by LCM1.0c follow-up 2: LCM1.0c
# (gwz-py afcd5a3, 2026-09-05) moved scripts/check_protocol_drift.py to the
# local-clone allocation's fingerprint 3c34bd74... but left this pin on the
# pre-LCM1.0c value, so this test was RED on every clean tree from afcd5a3
# until now. Because the pin below is the fingerprint of the follow-up 2
# schema, the LCM1.0c move is folded into this one: both allocations
# (LCM1.0c -- ActionKind 27/28, LocalCloneMode, LocalFamilyOp, the four
# local-family messages, the two service methods, MergeRequest
# .local_source_name (slot 9); follow-up 2 -- CloneLocalWorkspaceRequest
# .copy_source (tag 6), LocalFamilyResponse.members (tag 2) with
# LocalFamilyMemberEntry and the LocalMemberKind / LocalMemberState /
# LocalObservedState enums, GwzErrorCode.unknown_local (62)) were MEASURED
# additive on both trees by gwz-core's protocol/check_log_additive.py (242
# added / 0 removed, then 128 added / 0 removed). Kept identical to gwz-core
# protocol/check_log_additive.py and scripts/check_protocol_drift.py; every
# gwz-py fast suite that pins a protocol hash is listed in the LCM1.0c
# checkpoint record §11 so a move cannot skip one again.
#   was: 71bf6b9223ba6d2b4d12049e425e567254ca79396d67922be737c86c6dd97a40
#   (LCM1.0c's value, never pinned here: 3c34bd741b32f366f63928211eec83c920b5b0ca0ed1d847447f4d3428c22031)
#
# Moved deliberately again on 2026-09-06 by LCM1.0c follow-up 3 (operator
# ruling 3 of 2026-09-06, checkpoint record §12): LocalFamilyResponse
# .root_path (tag 3, optional), the family root's path a driver joins with
# each member's root-relative `path`. MEASURED additive on both trees by
# gwz-core's protocol/check_log_additive.py (11 added / 0 removed, the one
# field object). Kept identical to gwz-core protocol/check_log_additive.py
# and scripts/check_protocol_drift.py.
#   was: 26f0d16ffebdcdc26bbbe682a6347688781cd202694333dbb0c066d087fb6b4e
#
# Moved deliberately again on 2026-09-06 by LCM1.1 fix 1 (lane C, gwz-dev
# dev-docs/GwzLocalClone-LCM1.0c-Checkpoint.md §14; GwzLocalCloneDesign.md
# §4, §4.0, §4.1, §12), which allocates exactly four more GwzErrorCode
# members for the local-create outcomes LCM1.1's wiring had folded into
# unsupported_operation and io_error: unsupported_source_layout (63),
# copy_failed (64), source_drift (65) and destination_incomplete (66). No
# message, field or slot changed. MEASURED additive, not assumed: gwz-core's
# own protocol/check_log_additive.py rendered the projection on both trees
# and diffed them -- 4 added lines, 0 removed, 3 hunks, the four enum members
# as map keys -- and the previous pin reproduced exactly on the
# pre-allocation schema (gwz-core 81fcaf2). The three pins are one
# fingerprint of one schema. Kept identical to gwz-core
# protocol/check_log_additive.py and scripts/check_protocol_drift.py.
#   was: 2eca6469ed1281e77a95f1e419aa4065002aa94c77507a73ada6f6f9c8bb5503
#
# Moved deliberately again on 2026-09-06 by LCM1.2 (lane C, gwz-dev
# dev-docs/GwzLocalClone-LCM1.0c-Checkpoint.md §16; GwzLocalCloneDesign.md
# §6, §6.2, §12), which allocates exactly two more GwzErrorCode members for
# the family-merge import outcomes: pairing_mismatch (67) and
# import_incomplete (68). No message, field or slot changed. MEASURED
# additive, not assumed: gwz-core's own protocol/check_log_additive.py
# rendered the projection on both trees and diffed them -- 2 added lines, 0
# removed, 2 hunks, the two enum members as map keys -- and the previous pin
# reproduced exactly on the pre-allocation schema (gwz-core 63f1332). Kept
# identical to gwz-core protocol/check_log_additive.py and
# scripts/check_protocol_drift.py.
#   was: 0a173de982aaa93225e26581d678b4722356afc967fb4543de531708900cf981
#
# Moved deliberately again on 2026-09-06 by LCM2.1/LCM2.2 (lane C, gwz-dev
# dev-docs/GwzLocalClone-LCM1.0c-Checkpoint.md §17; GwzLocalCloneDesign.md
# §5, §5.1, §5.2, §12), which allocates exactly three more GwzErrorCode
# members for the ordinary-disposal outcomes: unwaived_hazard (69),
# unknown_evidence (70) and disposal_incomplete (71). No message, field or
# slot changed. MEASURED additive, not assumed: gwz-core's own
# protocol/check_log_additive.py rendered the projection on both trees and
# diffed them -- 3 added lines, 0 removed, 2 hunks, the three enum members as
# map keys -- and the previous pin reproduced exactly on the pre-allocation
# schema (gwz-core 6d1a28e). Kept identical to gwz-core
# protocol/check_log_additive.py and scripts/check_protocol_drift.py.
#   was: ba55594fa54123b865e06eb4bedfbf1eba4c9f52467a468831f9b699df0763a2
PRE_LOG_WIRE_SHA256 = "e99ce51a85b439fb03bb43df5beb3a33156048b8212d3f2fc609ba2db163db32"


def _round_trip(message_name: str, value: object) -> None:
    payload = to_wire(value)
    encoded = wire_codec.encode(schema(), message_name, payload)
    assert from_wire(message_name, wire_codec.decode(schema(), message_name, encoded)) == value
    assert decode_message(message_name, encode_message(message_name, value)) == value


def _meta() -> generated.RequestMeta:
    return generated.RequestMeta(
        request_id="req-log",
        schema_version="gwz.protocol/v0",
        workspace=None,
        selection=None,
        policy=None,
        dry_run=None,
        attribution=None,
    )


def test_log_uses_a_shape_log_stream_instead_of_paged_responses() -> None:
    request = generated.LogRequest(
        meta=_meta(),
        workspace_cwd="gwz-py",
        operands=["main..HEAD"],
        explicit_pathspecs=["src"],
        options=generated.LogOptions(
            max_entries=25,
            since="2026-08-01",
            until=None,
            author="Author",
            grep="protocol",
            no_merges=True,
            first_parent=False,
            strict=True,
            coalesce=False,
            include_body=True,
        ),
        tagged=False,
    )
    _round_trip("LogRequest", request)

    methods = {method.name: method for method in schema().services["GwzCore"].methods}
    assert methods["log"].shape == "unary"
    assert methods["log.output"].shape == "log"
    assert {
        "LogRequest",
        "LogResponse",
        "LogEntry",
        "LogDegradation",
        "LogOutputRecord",
    } <= schema().messages.keys()
    assert generated.ActionKind.log.value == 26
    assert generated.ActionKind.merge.value == 25


def test_log_output_stream_discriminates_entries_and_degradations() -> None:
    identity = generated.GitObjectIdentity(
        name="Author",
        email="author@example.invalid",
        time_ms=1_727_000_000_000,
        timezone_offset_minutes=600,
    )
    entry_record = generated.LogOutputRecord(
        kind=generated.LogOutputRecordKind.entry,
        entry=generated.LogEntry(
            members=[
                generated.LogEntryMember(
                    member_id="mem_core",
                    member_path="gwz-core",
                    source_kind=generated.SourceKind.git,
                    commit="0123456789abcdef0123456789abcdef01234567",
                    parents=["1111111111111111111111111111111111111111"],
                )
            ],
            provenance=generated.LogMergeProvenance(
                kind=generated.LogMergeKind.marker,
                gwz_commit_id="01987b0c-2f75-7c4a-9a32-8fd22f7d7c91",
            ),
            author=identity,
            committer=identity,
            subject="Add log protocol",
            body="Protocol-only body",
            ordering_timestamp_ms=1_727_000_000_000,
            author_timestamp_seconds=1_727_000_000,
            committer_timestamp_seconds=1_727_000_000,
            ordering_timestamp_seconds=1_727_000_000,
            lossy=False,
        ),
        degradation=None,
    )
    _round_trip("LogOutputRecord", entry_record)

    extreme_entry = deepcopy(entry_record)
    assert extreme_entry.entry is not None
    extreme_entry.entry.ordering_timestamp_ms = None
    extreme_entry.entry.author_timestamp_seconds = -(2**63)
    extreme_entry.entry.committer_timestamp_seconds = 2**63 - 1
    extreme_entry.entry.ordering_timestamp_seconds = 2**63 - 1
    extreme_entry.entry.lossy = True
    _round_trip("LogOutputRecord", extreme_entry)

    degradation_record = generated.LogOutputRecord(
        kind=generated.LogOutputRecordKind.degradation,
        entry=None,
        degradation=generated.LogDegradation(
            member_id="@root",
            member_path=".",
            source_kind=generated.SourceKind.git,
            reason=generated.LogDegradationReason.snapshot_entry_missing,
            operand="+release",
            message="snapshots do not record the workspace root",
        ),
    )
    _round_trip("LogOutputRecord", degradation_record)


def test_log_addition_preserves_every_pre_existing_wire_shape_and_slot() -> None:
    ir_path = Path(generated.__file__).with_name("gwz.ir.json")
    projected = deepcopy(json.loads(ir_path.read_text(encoding="utf-8")))
    projected["messages"] = [m for m in projected["messages"] if not m["name"].startswith("Log")]
    projected["enums"] = [e for e in projected["enums"] if not e["name"].startswith("Log")]
    actions = next(e for e in projected["enums"] if e["name"] == "ActionKind")["members"]
    assert actions.pop("log") == 26
    service = next(s for s in projected["services"] if s["name"] == "GwzCore")
    service["methods"] = [m for m in service["methods"] if m["name"] not in {"log", "log.output"}]
    encoded = json.dumps(projected, sort_keys=True, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == PRE_LOG_WIRE_SHA256
