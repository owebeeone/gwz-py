from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_protocol_drift.py"
IR = ROOT / "src" / "gwz" / "protocol" / "generated" / "gwz.ir.json"

spec = importlib.util.spec_from_file_location("protocol_drift", SCRIPT)
protocol_drift = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(protocol_drift)


def test_pre_log_projection_preserves_the_pinned_baseline_and_detects_old_shape_drift() -> None:
    protocol = json.loads(IR.read_text(encoding="utf-8"))
    assert (
        protocol_drift.fingerprint(protocol_drift.pre_log_projection(protocol))
        == protocol_drift.PRE_LOG_WIRE_FINGERPRINT
    )

    changed = deepcopy(protocol)
    request_meta = next(message for message in changed["messages"] if message["name"] == "RequestMeta")
    next(field for field in request_meta["fields"] if field["name"] == "request_id")["tag"] = 99
    assert (
        protocol_drift.fingerprint(protocol_drift.pre_log_projection(changed))
        != protocol_drift.PRE_LOG_WIRE_FINGERPRINT
    )
