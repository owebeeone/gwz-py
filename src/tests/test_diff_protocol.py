"""Round-trip coverage for the D0 `gwz diff` protocol messages.

These messages (DiffRequest, DiffOutputRecord, DiffParsedTarget,
DiffOutputLogRef, ...) landed in the taut schema and the regenerated
`gwz.protocol.generated` package. D0's acceptance requires that the Python
generated protocol classes round-trip the same diff messages the Rust side
does, so this exercises the real gwz-py codec path
(``encode_message`` / ``decode_message`` and the ``taut.wire`` CBOR codec)
rather than a bespoke encoder.
"""

from __future__ import annotations

from taut.wire import codec as wire_codec

from gwz.protocol import generated
from gwz.protocol.codec import (
    decode_message,
    encode_message,
    from_wire,
    generated_classes,
    schema,
    to_wire,
)


def _assert_cbor_round_trip(message_name: str, value: object) -> None:
    """Encode -> decode through the packaged IR + wire codec, both codec entry
    points, and assert the dataclass survives byte-exactly."""
    payload = to_wire(value)
    encoded = wire_codec.encode(schema(), message_name, payload)
    decoded_payload = wire_codec.decode(schema(), message_name, encoded)

    assert from_wire(message_name, decoded_payload) == value
    assert decode_message(message_name, encode_message(message_name, value)) == value


def test_generated_registry_exposes_all_diff_messages_and_action_kind() -> None:
    classes = generated_classes()

    # ActionKind gained the diff action (=21), keeping list_snapshots (=20).
    assert generated.ActionKind.diff.value == 21
    assert generated.ActionKind.list_snapshots.value == 20

    # Every D0 diff message/enum is registered and importable.
    for name in (
        "DiffRequest",
        "DiffOptions",
        "DiffComparison",
        "DiffRepoScope",
        "DiffExcludedTarget",
        "DiffParsedTarget",
        "DiffFileEntry",
        "DiffRepoSummary",
        "DiffSummary",
        "DiffOutputLogRef",
        "DiffOutputRecord",
        "DiffManifestResponse",
    ):
        assert name in classes, name
        assert name in schema().messages, name

    # The diff method is wired into the GwzCore service.
    methods = {method.name for method in schema().services["GwzCore"].methods}
    assert "diff" in methods


def _request_meta() -> generated.RequestMeta:
    return generated.RequestMeta(
        request_id="req-1",
        schema_version="0",
        workspace=None,
        selection=None,
        policy=None,
        dry_run=None,
        attribution=None,
    )


def test_minimal_diff_request_round_trip() -> None:
    request = generated.DiffRequest(
        meta=_request_meta(),
        workspace_cwd=None,
        operands=[],
        explicit_pathspecs=[],
        options=None,
        cached=None,
        merge_base=None,
        tagged=None,
    )

    _assert_cbor_round_trip("DiffRequest", request)


def test_diff_request_first_class_cached_and_merge_base_round_trip() -> None:
    request = generated.DiffRequest(
        meta=_request_meta(),
        workspace_cwd=None,
        operands=["A...B"],
        explicit_pathspecs=[],
        options=None,
        cached=True,
        merge_base=True,
        tagged=True,
    )

    _assert_cbor_round_trip("DiffRequest", request)


def test_diff_output_record_preserves_nul_laden_data_bytes() -> None:
    # Binary patch payload with embedded NULs and high bytes must survive intact.
    nul_bytes = b"@@ -1 +1 @@\n\x00\x01\x02patch\x00tail\xff\x00"
    record = generated.DiffOutputRecord(
        kind=generated.DiffOutputRecordKind.patch_bytes,
        scope=generated.DiffRepoScope(
            root=None,
            member_id="mem_app",
            member_path="repos/app",
            source_kind=generated.SourceKind.git,
        ),
        file_id="f1",
        entry=None,
        data=nul_bytes,
        stale=None,
        diagnostic=None,
    )

    # Guard the property the task calls out, then the full round-trip.
    decoded = decode_message("DiffOutputRecord", encode_message("DiffOutputRecord", record))
    assert decoded.data == nul_bytes
    assert decoded.data.count(0) == nul_bytes.count(0)
    _assert_cbor_round_trip("DiffOutputRecord", record)


def test_scoped_diff_parsed_target_preserves_snapshot_ids() -> None:
    target = generated.DiffParsedTarget(
        target_id="t0",
        scope=generated.DiffRepoScope(
            root=None,
            member_id="mem_app",
            member_path="repos/app",
            source_kind=generated.SourceKind.git,
        ),
        comparison=generated.DiffComparison(
            kind=generated.DiffComparisonKind.tree_vs_tree,
            left="+base",
            right="+tip",
            merge_base=None,
        ),
        pathspecs=["src/"],
        left_oid="aaaa1111",
        right_oid="bbbb2222",
        merge_base_oid=None,
        left_snapshot_id="base",
        right_snapshot_id="tip",
    )

    decoded = decode_message("DiffParsedTarget", encode_message("DiffParsedTarget", target))
    assert decoded.left_snapshot_id == "base"
    assert decoded.right_snapshot_id == "tip"
    _assert_cbor_round_trip("DiffParsedTarget", target)


def test_diff_output_log_ref_round_trip() -> None:
    log_ref = generated.DiffOutputLogRef(
        log_id="log-42",
        format=generated.DiffOutputFormat.patch,
        encoding=generated.DiffChunkEncoding.utf8,
    )

    _assert_cbor_round_trip("DiffOutputLogRef", log_ref)
