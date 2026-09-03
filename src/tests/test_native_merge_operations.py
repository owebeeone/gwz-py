from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gwz.errors import GwzOperationError
from gwz.protocol.codec import decode_message, encode_message
from gwz.protocol.generated import (
    AggregateStatus,
    EventKind,
    GitObjectIdentity,
    GwzErrorCode,
    MergeMode,
    MergeOp,
    MergeRequest,
    OperationAttribution,
    OperationEvent,
    OperationResult,
    RequestMeta,
    Selection,
    WorkspaceRef,
)

from native_helpers import (
    commit_file,
    create_workspace_with_member,
    git,
    native_client,
    native_module,
)


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
        filesystem_strict=None,
    )


def submit(native, request: MergeRequest):
    payload = native.submit(
        "merge",
        "MergeRequest",
        "MergeResponse",
        encode_message("MergeRequest", request),
    )
    return decode_message("MergeResponse", bytes(payload))


def invalid_attribution() -> OperationAttribution:
    return OperationAttribution(
        actor=None,
        git_author=GitObjectIdentity(
            name="",
            email="author@example.invalid",
            time_ms=None,
            timezone_offset_minutes=None,
        ),
        git_committer=None,
        credential_ref=None,
    )


def terminal_result(native, operation_id: str) -> OperationResult:
    return decode_message(
        "OperationResult", bytes(native.operation_result(operation_id))
    )


def operation_events(native, operation_id: str) -> list[OperationEvent]:
    return [
        decode_message("OperationEvent", bytes(payload))
        for payload in native.subscribe_events(operation_id)
    ]


def drain_operation_events(native, operation_id: str) -> list[OperationEvent]:
    events: list[OperationEvent] = []
    next_sequence = 0
    while True:
        payloads, complete = native.wait_events(operation_id, next_sequence, 5_000)
        batch = [
            decode_message("OperationEvent", bytes(payload)) for payload in payloads
        ]
        events.extend(batch)
        if batch:
            next_sequence = batch[-1].sequence + 1
        if complete:
            return events


def assert_failure_lifecycle(
    native,
    operation_id: str,
    expected_code: GwzErrorCode,
    message_fragment: str,
) -> OperationResult:
    result = terminal_result(native, operation_id)
    events = operation_events(native, operation_id)
    assert [event.kind for event in events] == [
        EventKind.operation_started,
        EventKind.operation_finished,
    ]
    assert [event.sequence for event in events] == [0, 1]
    assert result.aggregate_status is AggregateStatus.failed
    assert len(result.errors) == 1
    assert result.errors[0].code is expected_code
    assert message_fragment in result.errors[0].message
    with pytest.raises(RuntimeError, match="without a successful merge response"):
        native.merge_operation_response(operation_id)
    return result


def workspace_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def write_open_record(root: Path, state: str = "awaiting_resolution") -> None:
    record_dir = root / ".gwz" / "merge"
    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / "merge_test.yaml").write_text(
        f"""schema: gwz.merge-operation/v0
record_schema_version: 0
writer_version: test
workspace_id: ws_test
merge_id: merge_test
operation_id: op_original
state: {state}
source_ref: feature/source
created_at: now
baseline: {{ lock_sha256: lock, manifest_sha256: manifest }}
selected_targets: []
participants: {{}}
""",
        encoding="utf-8",
    )


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


@pytest.mark.parametrize("submitted", [False, True], ids=["synchronous", "submitted"])
def test_invalid_attribution_failure_has_one_lifecycle_and_original_error(
    tmp_path: Path,
    submitted: bool,
) -> None:
    native = native_module()
    client = native_client(tmp_path)
    asyncio.run(client.create_workspace(workspace_id=f"ws_invalid_attribution_{submitted}"))
    request = merge_request(
        tmp_path, f"req_invalid_attribution_{submitted}", MergeOp.status
    )
    request.meta.attribution = invalid_attribution()
    operation_id = f"op_{request.meta.request_id}"
    encoded = encode_message("MergeRequest", request)

    if submitted:
        accepted = submit(native, request)
        assert accepted.response.meta.operation_id == operation_id
    else:
        with pytest.raises(RuntimeError) as failure:
            native.call("merge", "MergeRequest", "MergeResponse", encoded)
        assert getattr(failure.value, "code") == "InvalidRequest"
        assert "git_identity.name" in getattr(failure.value, "machine_message")

    result = assert_failure_lifecycle(
        native,
        operation_id,
        GwzErrorCode.invalid_request,
        "git_identity.name",
    )
    assert result.attribution == request.meta.attribution
    assert all(
        event.attribution == request.meta.attribution
        for event in operation_events(native, operation_id)
    )


@pytest.mark.parametrize("submitted", [False, True], ids=["synchronous", "submitted"])
@pytest.mark.parametrize("dry_run", [False, True], ids=["real", "dry-run"])
@pytest.mark.parametrize(
    "state",
    ["awaiting_resolution", "halted", "recovery_required", "finalizing"],
)
def test_open_operation_failure_completes_after_one_lifecycle_without_mutation(
    tmp_path: Path,
    submitted: bool,
    dry_run: bool,
    state: str,
) -> None:
    native = native_module()
    client = native_client(tmp_path)
    asyncio.run(
        client.create_workspace(
            workspace_id=f"ws_open_{state}_{submitted}_{dry_run}"
        )
    )
    write_open_record(tmp_path, state)
    request = merge_request(
        tmp_path, f"req_open_{state}_{submitted}_{dry_run}", MergeOp.start
    )
    request.source_ref = "feature/source"
    request.meta.dry_run = dry_run
    operation_id = f"op_{request.meta.request_id}"
    before = workspace_bytes(tmp_path)

    if submitted:
        accepted = submit(native, request)
        assert accepted.response.meta.operation_id == operation_id
    else:
        with pytest.raises(RuntimeError) as failure:
            native.call(
                "merge",
                "MergeRequest",
                "MergeResponse",
                encode_message("MergeRequest", request),
            )
        assert getattr(failure.value, "code") == "OpenOperation"

    assert_failure_lifecycle(
        native,
        operation_id,
        GwzErrorCode.open_operation,
        "merge_test",
    )
    assert workspace_bytes(tmp_path) == before


@pytest.mark.parametrize(
    ("failure_kind", "expected_code", "native_code", "message_fragment"),
    [
        (
            "backend",
            GwzErrorCode.git_command_failed,
            "GitCommandFailed",
            "not found",
        ),
        (
            "store",
            GwzErrorCode.merge_record_unreadable,
            "MergeRecordUnreadable",
            "invalid YAML",
        ),
    ],
)
@pytest.mark.parametrize("submitted", [False, True], ids=["synchronous", "submitted"])
def test_backend_and_store_failures_complete_with_structured_errors_without_mutation(
    tmp_path: Path,
    failure_kind: str,
    expected_code: GwzErrorCode,
    native_code: str,
    message_fragment: str,
    submitted: bool,
) -> None:
    native = native_module()
    request_id = f"req_native_{failure_kind}_failure_{submitted}"
    if failure_kind == "backend":
        create_workspace_with_member(tmp_path)
        request = merge_request(tmp_path, request_id, MergeOp.start)
        request.source_ref = "feature/does-not-exist"
        request.meta.selection = Selection(
            all=None,
            member_ids=["mem_app"],
            paths=[],
            targets=[],
            exclude_targets=[],
        )
    else:
        client = native_client(tmp_path)
        asyncio.run(client.create_workspace(workspace_id="ws_native_store_failure"))
        record_dir = tmp_path / ".gwz" / "merge"
        record_dir.mkdir(parents=True, exist_ok=True)
        (record_dir / "broken.yaml").write_text("{", encoding="utf-8")
        request = merge_request(tmp_path, request_id, MergeOp.status)

    before = workspace_bytes(tmp_path)
    operation_id = f"op_{request_id}"
    if submitted:
        accepted = submit(native, request)
        assert accepted.response.meta.operation_id == operation_id
    else:
        with pytest.raises(RuntimeError) as failure:
            native.call(
                "merge",
                "MergeRequest",
                "MergeResponse",
                encode_message("MergeRequest", request),
            )
        assert getattr(failure.value, "code") == native_code

    assert operation_id == f"op_{request_id}"
    assert_failure_lifecycle(
        native,
        operation_id,
        expected_code,
        message_fragment,
    )
    assert workspace_bytes(tmp_path) == before


def test_submitted_failure_wakes_multiple_event_and_result_waiters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = native_module()
    client = native_client(tmp_path)
    asyncio.run(client.create_workspace(workspace_id="ws_merge_waiters"))
    request = merge_request(tmp_path, "req_merge_waiters", MergeOp.status)
    request.meta.attribution = invalid_attribution()
    monkeypatch.setenv("GWZ_PY_TEST_EVENT_DELAY_MS", "250")
    accepted = submit(native, request)
    operation_id = accepted.response.meta.operation_id
    assert operation_id == "op_req_merge_waiters"

    with ThreadPoolExecutor(max_workers=4) as executor:
        result_futures = [
            executor.submit(native.operation_result, operation_id) for _ in range(2)
        ]
        event_futures = [
            executor.submit(drain_operation_events, native, operation_id)
            for _ in range(2)
        ]
        results = [
            decode_message("OperationResult", bytes(future.result(timeout=5)))
            for future in result_futures
        ]
        event_histories = [future.result(timeout=5) for future in event_futures]

    assert results[0] == results[1]
    assert results[0].errors[0].code is GwzErrorCode.invalid_request
    assert all(
        [event.kind for event in events]
        == [EventKind.operation_started, EventKind.operation_finished]
        for events in event_histories
    )


def test_closing_one_event_iterator_does_not_own_submitted_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = native_client(tmp_path)
    asyncio.run(client.create_workspace(workspace_id="ws_merge_resubscribe"))
    monkeypatch.setenv("GWZ_PY_TEST_EVENT_DELAY_MS", "250")

    async def exercise() -> tuple[list[OperationEvent], OperationResult]:
        handle = await client.merge_stream(
            op=MergeOp.status,
            request_id="req_merge_resubscribe",
            attribution=invalid_attribution(),
        )
        abandoned = handle.events()
        first = await asyncio.wait_for(anext(abandoned), timeout=2)
        assert first.kind is EventKind.operation_started
        await abandoned.aclose()

        replay = [event async for event in handle.events()]
        with pytest.raises(GwzOperationError) as failure:
            await handle.result()
        return replay, failure.value.response

    events, result = asyncio.run(exercise())
    assert [event.kind for event in events] == [
        EventKind.operation_started,
        EventKind.operation_finished,
    ]
    assert result.aggregate_status is AggregateStatus.failed
    assert result.errors[0].code is GwzErrorCode.invalid_request


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

    result = terminal_result(native, operation_id)
    events = operation_events(native, operation_id)
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

    result = terminal_result(native, operation_id)
    events = operation_events(native, operation_id)
    assert [event.kind for event in events] == [
        EventKind.operation_started,
        EventKind.operation_finished,
    ]
    assert result.aggregate_status is AggregateStatus.failed
    assert result.errors[0].code is GwzErrorCode.git_command_failed
    assert result.errors[0].member_id == "mem_lib"
    assert result.errors[0].member_path == "repos/lib"
