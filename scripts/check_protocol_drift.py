#!/usr/bin/env python3
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
PRE_LOG_WIRE_FINGERPRINT = (
    "sha256:71bf6b9223ba6d2b4d12049e425e567254ca79396d67922be737c86c6dd97a40"
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
    return projected


def fail(message: str) -> None:
    print(f"check_protocol_drift: error: {message}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
