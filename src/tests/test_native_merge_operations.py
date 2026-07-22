from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gwz.protocol.codec import decode_message, encode_message
from gwz.protocol.generated import (
    AggregateStatus,
    EventKind,
    GwzErrorCode,
    MergeMode,
    MergeOp,
    MergeRequest,
    RequestMeta,
    Selection,
    WorkspaceRef,
)

from native_helpers import commit_file, git, native_client, native_module


def merge_request(root: Path, request_id: str, op: MergeOp) -> MergeRequest:
    return MergeRequest(
        meta=RequestMeta(
            request_id=request_id,
            schema_version="gwz.protocol/v0",
            workspace=WorkspaceRef(root=str(root), workspace_id=None),
            selection=None,
            policy=None,
            dry_run=None,
            attribution=None,
        ),
        op=op,
        source_ref=None,
        merge_id=None,
        mode=MergeMode.normal if op is MergeOp.start else None,
        message=None,
        preserve=None,
    )


def submit(native, request: MergeRequest):
    payload = native.submit(
        "merge",
        "MergeRequest",
        "MergeResponse",
        encode_message("MergeRequest", request),
    )
    return decode_message("MergeResponse", bytes(payload))


def test_submitted_merge_retains_response_before_completion_is_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    native = native_module()
    client = native_client(tmp_path)
    # Establish a real workspace without depending on the submitted merge path.
    asyncio.run(client.create_workspace(workspace_id="ws_merge_submit"))
    monkeypatch.setenv("GWZ_PY_TEST_EVENT_DELAY_MS", "250")
    accepted = submit(native, merge_request(tmp_path, "req_submit_status", MergeOp.status))
    operation_id = accepted.response.meta.operation_id
    assert operation_id == "op_req_submit_status"
    assert accepted.response.meta.aggregate_status is AggregateStatus.accepted

    with ThreadPoolExecutor(max_workers=3) as executor:
        result_future = executor.submit(native.operation_result, operation_id)
        response_futures = [
            executor.submit(native.merge_operation_response, operation_id) for _ in range(2)
        ]
        result = decode_message("OperationResult", bytes(result_future.result(timeout=5)))
        responses = [
            decode_message("MergeResponse", bytes(future.result(timeout=5)))
            for future in response_futures
        ]

    events = [
        decode_message("OperationEvent", bytes(payload))
        for payload in native.subscribe_events(operation_id)
    ]
    assert [event.kind for event in events] == [
        EventKind.operation_started,
        EventKind.operation_finished,
    ]
    assert result.aggregate_status is AggregateStatus.noop
    assert all(response == responses[0] for response in responses)
    assert responses[0].response.meta.request_id == "req_submit_status"


@pytest.mark.parametrize("submitted", [False, True])
def test_failed_merge_completes_once_with_the_original_structured_error(
    tmp_path: Path, submitted: bool
) -> None:
    native = native_module()
    client = native_client(tmp_path)
    asyncio.run(client.create_workspace(workspace_id=f"ws_merge_failure_{submitted}"))
    request = merge_request(tmp_path, f"req_invalid_{submitted}", MergeOp.start)
    request.source_ref = ""
    encoded = encode_message("MergeRequest", request)
    operation_id = f"op_{request.meta.request_id}"

    if submitted:
        accepted = submit(native, request)
        assert accepted.response.meta.operation_id == operation_id
    else:
        with pytest.raises(RuntimeError) as failure:
            native.call("merge", "MergeRequest", "MergeResponse", encoded)
        assert getattr(failure.value, "code") == "MergeValidationFailed"

    result = decode_message(
        "OperationResult", bytes(native.operation_result(operation_id))
    )
    events = [
        decode_message("OperationEvent", bytes(payload))
        for payload in native.subscribe_events(operation_id)
    ]
    assert [event.kind for event in events] == [
        EventKind.operation_started,
        EventKind.operation_finished,
    ]
    assert result.aggregate_status is AggregateStatus.failed
    assert len(result.errors) == 1
    assert result.errors[0].code is GwzErrorCode.merge_validation_failed
    assert "source_ref" in result.errors[0].message
    with pytest.raises(RuntimeError, match="without a successful merge response"):
        native.merge_operation_response(operation_id)


def test_duplicate_merge_operation_id_is_rejected_without_overwriting_result(
    tmp_path: Path,
) -> None:
    native = native_module()
    client = native_client(tmp_path)
    asyncio.run(client.create_workspace(workspace_id="ws_merge_duplicate"))
    request = merge_request(tmp_path, "req_duplicate", MergeOp.status)
    first = submit(native, request)
    operation_id = first.response.meta.operation_id
    assert operation_id is not None
    first_result = bytes(native.operation_result(operation_id))

    with pytest.raises(RuntimeError, match="already exists"):
        submit(native, request)

    assert bytes(native.operation_result(operation_id)) == first_result
    events = list(native.subscribe_events(operation_id))
    assert len(events) == 2


def test_submitted_preflight_failure_retains_member_context(tmp_path: Path) -> None:
    native = native_module()
    client = native_client(tmp_path)
    asyncio.run(client.create_workspace(workspace_id="ws_merge_member_failure"))
    asyncio.run(client.create_repo("repos/app", member_id="mem_app", source_id="src_app"))
    asyncio.run(client.create_repo("repos/lib", member_id="mem_lib", source_id="src_lib"))
    app = tmp_path / "repos" / "app"
    lib = tmp_path / "repos" / "lib"
    commit_file(app, "README.md", "app\n", "initial app")
    commit_file(lib, "README.md", "lib\n", "initial lib")
    git(app, "branch", "feature/source")
    asyncio.run(client.capture(paths=["repos/app", "repos/lib"]))

    request = merge_request(tmp_path, "req_member_failure", MergeOp.start)
    request.source_ref = "feature/source"
    request.meta.selection = Selection(
        all=None,
        member_ids=["mem_app", "mem_lib"],
        paths=[],
        targets=[],
        exclude_targets=[],
    )
    accepted = submit(native, request)
    operation_id = accepted.response.meta.operation_id
    assert operation_id is not None

    result = decode_message(
        "OperationResult", bytes(native.operation_result(operation_id))
    )
    events = [
        decode_message("OperationEvent", bytes(payload))
        for payload in native.subscribe_events(operation_id)
    ]
    assert [event.kind for event in events] == [
        EventKind.operation_started,
        EventKind.operation_finished,
    ]
    assert result.aggregate_status is AggregateStatus.failed
    assert result.errors[0].code is GwzErrorCode.git_command_failed
    assert result.errors[0].member_id == "mem_lib"
    assert result.errors[0].member_path == "repos/lib"
