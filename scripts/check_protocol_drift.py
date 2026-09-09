#!/usr/bin/env python3
# DR-5 startup timeout: removing the service method and two messages exactly
# reproduces prior projection 9f338f2287cf7127b760b5dfaf4e86a5f5152fb38234fb9c5ca94949db3e271d.
# DR-5 observation fields: removing the message, three enums and two optional
# slots exactly reproduces prior projection f45ebbb8cfa1ed81f29cf18c4e6df03314ee45d4584229d2a8daa1b9e16bdc73.
# DR-5 local configuration: removing method, three messages, op enum and action 29
# exactly reproduces prior projection ab44d75d4ef6bca60864c7150c44f381c318aaa642db143aad619951fa4ff44a.
# DR-5 capability query: removing only its service method and two messages
# exactly reproduces previous projection 09f98f645608b84b2eb9dbaede79f2b0d3750e8e6c337f2b254eca2b0da990ce.
# DR-5 (2026-09-07): measured additive RemoteSshIdentity, TransportOptions,
# and optional RequestMeta.transport slot 8. Removing exactly these additions
# reproduced prior projection 6fd2f8829a920d6e4264a102f995a28ccc5d3dc47b25c66eca98980ad5488ca7.
"""Verify packaged GWZ protocol IR matches the linked gwz-core schema."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = ROOT.parent / "gwz-core" / "protocol" / "gwz.taut.py"
DEFAULT_IR = ROOT / "src" / "gwz" / "protocol" / "generated" / "gwz.ir.json"
# This pin exists to catch gwz-log reshaping an OLDER message; it is not a
# freeze on additive growth. Moved deliberately on 2026-09-03 by DR-1 ship (1)
# W1 (gwz-dev dev-docs/GwzM5-8DR1-WarnOrRefuse-Charter.md §3.7), which adds
# MergeRequest.filesystem_strict (slot 8), MergeResponse.crash_recovery
# (slot 11), the MergeCrashRecovery message, the MergeCrashRecoveryGap enum and
# EventKind.diagnostic (slot 8). Every pre-existing slot is unchanged, so the
# guard keeps working against gwz-log after the move.
#   was: sha256:d0c205c8767f8d54d32ead2f676a05077d849f6a12278d9de52b3c132c3c9372
#
# Moved deliberately again on 2026-09-04 following gwz-core's M5d step (3)
# (gwz-dev dev-docs/GwzM5-8M5d-Charter.md §3/§10.2), which allocates exactly
# one more optional response field, MergeCrashRecovery.handles_ok (slot 4).
# No version bump, no record or catalog format change. `pre_log_projection`
# strips only `Log*` messages, so MergeCrashRecovery is inside the projection
# and this pin has to move with it. MEASURED additive, not assumed: the
# projection was rendered from the packaged IR on both trees and diffed -- the
# only delta is the one new `handles_ok` field object, and the previous pin
# below reproduced exactly on the pre-regeneration tree.
#   was: sha256:7a66e301c5c0147a12c59b2cddb6f2ebc1515ef4d65297ec53c3b312a3769697
#
# Moved deliberately again on 2026-09-05 by LCM1.0c (gwz-dev
# dev-docs/GwzLocalCloneDesign.md §7, GwzLocalClonePlan.md §3 "1.0c"), which
# allocates the local clone family surface: ActionKind.clone_local_workspace
# (27) and local_family (28), the LocalCloneMode and LocalFamilyOp enums, the
# CloneLocalWorkspaceRequest/Response and LocalFamilyRequest/Response
# messages, their two GwzCore service methods, and the optional
# MergeRequest.local_source_name (slot 9). `pre_log_projection` strips only
# `Log*` items, so all of these are inside the projection and the pin moves
# with them. MEASURED additive, not assumed: gwz-core's own
# protocol/check_log_additive.py rendered the projection on both trees and
# diffed them -- 242 added lines, 0 removed, every added object one of the
# items above -- and the previous pin reproduced exactly on the pre-allocation
# schema (gwz-core 87207c2). The two pins are one fingerprint of one schema.
#   was: sha256:71bf6b9223ba6d2b4d12049e425e567254ca79396d67922be737c86c6dd97a40
#
# Moved deliberately again on 2026-09-05 by LCM1.0c follow-up 2 (gwz-dev
# dev-docs/GwzLocalCloneDesign.md revision 9 §7 and §11 items 11-13, the
# operator's rulings on the LCM1.0c checkpoint's §7 questions 1-3), which
# allocates CloneLocalWorkspaceRequest.copy_source (optional, tag 6),
# LocalFamilyResponse.members (tag 2) with its LocalFamilyMemberEntry message
# and the LocalMemberKind / LocalMemberState / LocalObservedState enums, and
# GwzErrorCode.unknown_local (62). `pre_log_projection` strips only `Log*`
# items, so all of these are inside the projection and the pin moves with
# them. MEASURED additive, not assumed: gwz-core's own
# protocol/check_log_additive.py rendered the projection on both trees and
# diffed them -- 128 added lines, 0 removed, 5 hunks, every added object one
# of the items above -- and the previous pin reproduced exactly on the
# pre-allocation schema (gwz-core 0d7b53d). The three pins (this one,
# gwz-core protocol/check_log_additive.py and src/tests/test_log_protocol.py)
# are one fingerprint of one schema.
#   was: sha256:3c34bd741b32f366f63928211eec83c920b5b0ca0ed1d847447f4d3428c22031
#
# Moved deliberately again on 2026-09-06 by LCM1.0c follow-up 3 (the
# operator's cross-driver ruling 3 of 2026-09-06, gwz-dev
# dev-docs/GwzLocalClone-LCM1.0c-Checkpoint.md §12, GwzLocalCloneDesign.md
# §7/§8.1), which allocates exactly one more optional response field,
# LocalFamilyResponse.root_path (tag 3): the family root's path, which a
# driver joins with each member's root-relative `path`. `pre_log_projection`
# strips only `Log*` items, so it is inside the projection and the pin moves
# with it. MEASURED additive, not assumed: gwz-core's own
# protocol/check_log_additive.py rendered the projection on both trees and
# diffed them -- 11 added lines, 0 removed, 1 hunk, the one `root_path` field
# object -- and the previous pin reproduced exactly on the pre-allocation
# schema (gwz-core 7e962d2). The three pins are one fingerprint of one schema.
#   was: sha256:26f0d16ffebdcdc26bbbe682a6347688781cd202694333dbb0c066d087fb6b4e
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
# fingerprint of one schema.
#   was: sha256:2eca6469ed1281e77a95f1e419aa4065002aa94c77507a73ada6f6f9c8bb5503
#
# Moved deliberately again on 2026-09-06 by LCM1.2 (lane C, gwz-dev
# dev-docs/GwzLocalClone-LCM1.0c-Checkpoint.md §16; GwzLocalCloneDesign.md
# §6, §6.2, §12), which allocates exactly two more GwzErrorCode members for
# the family-merge import outcomes: pairing_mismatch (67) and
# import_incomplete (68). No message, field or slot changed. MEASURED
# additive, not assumed: gwz-core's own protocol/check_log_additive.py
# rendered the projection on both trees and diffed them -- 2 added lines, 0
# removed, 2 hunks, the two enum members as map keys -- and the previous pin
# reproduced exactly on the pre-allocation schema (gwz-core 63f1332). The
# three pins are one fingerprint of one schema.
#   was: sha256:0a173de982aaa93225e26581d678b4722356afc967fb4543de531708900cf981
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
# schema (gwz-core 6d1a28e). The three pins are one fingerprint of one
# schema.
#   was: sha256:ba55594fa54123b865e06eb4bedfbf1eba4c9f52467a468831f9b699df0763a2
# Debt recovery adds only GwzCore.resolve_forall_targets using existing messages.
# Removing that method reproduces the prior e99ce51a85b439fb03bb43df5beb3a33156048b8212d3f2fc609ba2db163db32 pin exactly.
PRE_LOG_WIRE_FINGERPRINT = (
    "sha256:8aa25038218daf2d085b62bb37fb4438afd06bb77628746dac80efe53a56e76c"
)


def main() -> int:
    args = parse_args()
    schema = args.schema.resolve()
    packaged_ir = args.ir.resolve()
    if not schema.exists():
        fail(f"schema not found: {schema}")
    if not packaged_ir.exists():
        fail(f"packaged IR not found: {packaged_ir}")

    expected = export_schema_ir(schema)
    actual = json.loads(packaged_ir.read_text(encoding="utf-8"))
    expected_fingerprint = fingerprint(expected)
    actual_fingerprint = fingerprint(actual)
    if actual != expected:
        print("check_protocol_drift: packaged gwz.ir.json does not match gwz-core schema", file=sys.stderr)
        print(f"  schema:      {schema}", file=sys.stderr)
        print(f"  packaged IR: {packaged_ir}", file=sys.stderr)
        print(f"  expected:    {expected_fingerprint}", file=sys.stderr)
        print(f"  actual:      {actual_fingerprint}", file=sys.stderr)
        print("  run: python scripts/regen_protocol.py", file=sys.stderr)
        return 1

    pre_log_fingerprint = fingerprint(pre_log_projection(actual))
    if pre_log_fingerprint != PRE_LOG_WIRE_FINGERPRINT:
        print("check_protocol_drift: gwz-log changed a pre-existing wire shape", file=sys.stderr)
        print(f"  expected: {PRE_LOG_WIRE_FINGERPRINT}", file=sys.stderr)
        print(f"  actual:   {pre_log_fingerprint}", file=sys.stderr)
        return 1

    print(f"check_protocol_drift: OK {actual_fingerprint}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--ir", type=Path, default=DEFAULT_IR)
    return parser.parse_args()


def export_schema_ir(schema: Path) -> dict[str, Any]:
    add_local_taut_to_path()
    try:
        from taut.ir.export import schema_json
        from taut.ir.load import load_schema
    except ImportError as exc:
        fail(f"cannot import taut-proto: {exc}")
    return schema_json(load_schema(schema))


def add_local_taut_to_path() -> None:
    local_taut = ROOT.parent / "taut" / "src"
    if local_taut.exists():
        sys.path.insert(0, str(local_taut))
    os.environ.setdefault("SETUPTOOLS_SCM_PRETEND_VERSION", "0.0.0")
    os.environ.setdefault("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_TAUT_PROTO", "0.0.0")


def fingerprint(value: dict[str, Any]) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def pre_log_projection(value: dict[str, Any]) -> dict[str, Any]:
    """Strip only S2.0 additions so every older message/slot is fingerprinted."""
    projected = json.loads(json.dumps(value))
    projected["messages"] = [
        message for message in projected["messages"] if not message["name"].startswith("Log")
    ]
    projected["enums"] = [
        enum for enum in projected["enums"] if not enum["name"].startswith("Log")
    ]
    actions = next(enum for enum in projected["enums"] if enum["name"] == "ActionKind")
    if actions["members"].pop("log", None) != 26:
        fail("ActionKind.log must occupy the next additive slot 26")
    service = next(service for service in projected["services"] if service["name"] == "GwzCore")
    service["methods"] = [
        method for method in service["methods"] if method["name"] not in {"log", "log.output"}
    ]
    # 2026-09-10 private-member policy adds exactly these optional booleans.
    # Removing them must reproduce the unchanged historical projection hash.
    for name, tag in (("MemberSpec", 8), ("RepoSyncRequest", 2)):
        message = next(m for m in projected["messages"] if m["name"] == name)
        added = [f for f in message["fields"] if f["name"] == "private"]
        expected = {"name": "private", "tag": tag,
                    "type": {"k": "scalar", "scalar": "bool"},
                    "optional": True, "transient": False, "merge": None}
        if added != [expected]:
            raise ValueError(f"{name}.private must be the optional boolean at tag {tag}")
        message["fields"].remove(added[0])
    return projected


def fail(message: str) -> None:
    print(f"check_protocol_drift: error: {message}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
