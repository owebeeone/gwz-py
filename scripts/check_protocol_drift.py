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


def fail(message: str) -> None:
    print(f"check_protocol_drift: error: {message}", file=sys.stderr)
    raise SystemExit(1)


if __name__ == "__main__":
    raise SystemExit(main())
