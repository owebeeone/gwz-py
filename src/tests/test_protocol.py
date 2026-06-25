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
    assert {"repo_sync", "branch", "stash", "events.subscribe", "operation.result"} <= methods
    assert "forall" not in methods


def test_generated_runtime_artifacts_are_api_only() -> None:
    generated_dir = Path(generated.__file__).parent

    assert {path.name for path in generated_dir.iterdir() if path.is_file()} == {
        "__init__.py",
        "api.py",
        "gwz.ir.json",
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
