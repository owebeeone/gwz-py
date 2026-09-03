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
PRE_LOG_WIRE_SHA256 = "7a66e301c5c0147a12c59b2cddb6f2ebc1515ef4d65297ec53c3b312a3769697"


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
