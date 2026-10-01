"""Operation records and the request IDs that name them (the core session
contract, dev-docs/GwzCoreSessionDesign.md §4.3).

A request's ``request_id`` is unique among live operations: a request whose
``request_id`` matches a live operation's is refused with ``InvalidRequest``
before any effect. The extension names an operation ``op_<request_id>``, so a
request that reuses the ID of an operation that has ended gets a fresh record
under that name, never the ended operation's. These rows hold operations in
the extension's event-delay hook, so they need no network fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gwz.protocol.codec import decode_message
from gwz.protocol.generated import AggregateStatus, EventKind
from host_helpers import (
    DELAY_VARIABLE,
    call,
    error_code,
    init_request,
    result,
    started,
    submit,
    wait_until,
)
from native_helpers import native_module


@pytest.fixture
def native():
    return native_module()


def missing(base: Path, name: str) -> str:
    """A source whose failure names it: the operation fails resolving it."""
    return f"file://{base / f'{name}-source'}"


def failure(native, operation_id: str) -> str:
    outcome = result(native, operation_id)
    assert outcome.aggregate_status is AggregateStatus.failed
    return " ".join(error.message for error in outcome.errors)


def event_kinds(native, operation_id: str) -> list[EventKind]:
    return [
        decode_message("OperationEvent", bytes(payload)).kind
        for payload in native.subscribe_events(operation_id)
    ]


def test_a_request_id_reused_after_its_operation_ended_gets_a_fresh_record(
    native, tmp_path: Path
) -> None:
    host = native.ClientHost()
    first = submit(host, init_request(tmp_path, "req_reused", missing(tmp_path, "first")))
    assert "first-source" in failure(native, first)
    first_events = event_kinds(native, first)

    second = submit(host, init_request(tmp_path, "req_reused", missing(tmp_path, "second")))
    assert second == first, "the operation ID follows the request ID"
    outcome = failure(native, second)
    assert "second-source" in outcome, "the second operation's own outcome, not the first's"
    assert "first-source" not in outcome
    assert event_kinds(native, second) == first_events, "its own events, and none of the first's"
    host.close()


def test_a_request_id_that_matches_a_live_operation_is_refused_before_any_effect(
    native, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(DELAY_VARIABLE, "2000")
    host = native.ClientHost()
    # Another Client: its host has its own operations, so only the record
    # store sees that the request ID names a live operation.
    other = native.ClientHost()
    running = submit(host, init_request(tmp_path, "req_live", missing(tmp_path, "running")))
    wait_until(lambda: started(native, running), 5, "the operation starting")
    duplicate = tmp_path / "duplicate"
    duplicate.mkdir()
    for entry in (submit, call):
        with pytest.raises(RuntimeError) as refused:
            entry(other, init_request(duplicate, "req_live", missing(duplicate, "duplicate")))
        assert error_code(refused.value) == "InvalidRequest", refused.value
        assert "op_req_live" in str(refused.value)
    assert list(duplicate.iterdir()) == [], "the refused requests had no effect"

    outcome = failure(native, running)
    assert "running-source" in outcome and "duplicate-source" not in outcome
    kinds = event_kinds(native, running)
    assert kinds.count(EventKind.operation_started) == 1, "one operation's events only"
    assert kinds.count(EventKind.operation_finished) == 1
    host.close()
    other.close()


def test_a_failed_call_ends_its_record_so_its_request_id_can_run_again(
    native, tmp_path: Path
) -> None:
    host = native.ClientHost()
    with pytest.raises(RuntimeError) as failed:
        call(host, init_request(tmp_path, "req_called", missing(tmp_path, "called")))
    assert error_code(failed.value) == "GitCommandFailed"
    # The call's record ended with its failure, so the ID names no live
    # operation.
    with pytest.raises(RuntimeError) as recorded:
        native.try_operation_result("op_req_called")
    assert error_code(recorded.value) == "GitCommandFailed"
    assert "called-source" in str(recorded.value)
    again = submit(host, init_request(tmp_path, "req_called", missing(tmp_path, "again")))
    assert "again-source" in failure(native, again)
    host.close()
