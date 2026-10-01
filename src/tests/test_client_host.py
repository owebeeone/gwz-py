"""The native ``ClientHost`` (1.1.0 S6.2 and S6.3; gwz-py
dev-docs/GwzPyPerOperationTransportDesign.md §2.4–§2.6 and §3).

These rows hold network operations in the extension's event-delay hook, so
they need no network fixture and run against any build: the limit across
both entries, cancellation of a waiting operation, cancels that must fail,
close with a failing operation in flight, and interpreter exit. The
transport route's own rows are in ``test_client_host_transport.py``.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from gwz.protocol.codec import decode_message
from gwz.protocol.generated import AggregateStatus, GwzErrorCode
from host_helpers import (
    CLEANUP_BOUND,
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

LIMIT = 8
HOLD_MS = 2000


@pytest.fixture
def native():
    return native_module()


@pytest.fixture
def held(monkeypatch):
    """Every operation started from here sleeps HOLD_MS after it starts."""
    monkeypatch.setenv(DELAY_VARIABLE, str(HOLD_MS))


def test_a_ninth_operation_of_either_entry_waits_and_is_cancelled_while_it_waits(
    native, held, tmp_path: Path
) -> None:
    host = native.ClientHost()
    with ThreadPoolExecutor(max_workers=LIMIT) as threads:
        # Four of each entry fill the limit.
        submitted = [submit(host, init_request(tmp_path, f"req_submitted_{i}")) for i in range(4)]
        called = [
            threads.submit(call, host, init_request(tmp_path, f"req_called_{i}")) for i in range(4)
        ]
        running = submitted + [f"op_req_called_{i}" for i in range(4)]
        wait_until(lambda: all(started(native, op) for op in running), 5, "eight operations starting")

        # A ninth submit is accepted at once and waits on its own thread.
        begun = time.monotonic()
        waiting_submit = submit(host, init_request(tmp_path, "req_ninth_submit"))
        assert time.monotonic() - begun < 1
        # A ninth call waits on the thread that runs it.
        waiting_call = threads.submit(call, host, init_request(tmp_path, "req_ninth_call"))
        time.sleep(0.3)
        assert not started(native, waiting_submit)
        assert not waiting_call.done()
        assert native.try_operation_result(waiting_submit) is None

        # Cancelled while it waits, each is refused with Cancelled and built
        # nothing, so its report is (0, false).
        assert host.cancel_operation(waiting_submit) == (0, False)
        assert host.cancel_operation("op_req_ninth_call") == (0, False)
        refused = result(native, waiting_submit)
        assert refused.aggregate_status is AggregateStatus.failed
        assert [error.code for error in refused.errors] == [GwzErrorCode.cancelled]
        with pytest.raises(RuntimeError) as cancelled_call:
            waiting_call.result(timeout=5)
        assert error_code(cancelled_call.value) == "Cancelled"
        assert not started(native, waiting_submit)

        # The eight were not cancelled: each fails on its missing source.
        for operation in submitted:
            outcome = result(native, operation)
            assert [error.code for error in outcome.errors] != [GwzErrorCode.cancelled]
        for future in called:
            with pytest.raises(RuntimeError) as failed:
                future.result(timeout=10)
            assert error_code(failed.value) != "Cancelled"


def test_a_waiting_operation_runs_when_a_slot_frees(native, held, tmp_path: Path) -> None:
    host = native.ClientHost()
    running = [submit(host, init_request(tmp_path, f"req_full_{i}")) for i in range(LIMIT)]
    wait_until(lambda: all(started(native, op) for op in running), 5, "eight operations starting")
    ninth = submit(host, init_request(tmp_path, "req_next"))
    freed = wait_until(lambda: native.try_operation_result(running[0]) is not None, 10, "a slot freeing")
    # It started once the first eight ended, and was not refused.
    wait_until(lambda: started(native, ninth), 5, "the ninth starting")
    assert freed > 0
    assert [error.code for error in result(native, ninth).errors] != [GwzErrorCode.cancelled]


def test_each_client_host_has_its_own_limit(native, held, tmp_path: Path) -> None:
    full = native.ClientHost()
    other = native.ClientHost()
    running = [submit(full, init_request(tmp_path, f"req_full_host_{i}")) for i in range(LIMIT)]
    wait_until(lambda: all(started(native, op) for op in running), 5, "eight operations starting")
    elsewhere = submit(other, init_request(tmp_path, "req_other_host"))
    wait_until(lambda: started(native, elsewhere), 1.5, "another host's operation starting")


def test_a_cancel_naming_a_wrong_foreign_completed_or_non_network_operation_fails(
    native, held, tmp_path: Path
) -> None:
    host = native.ClientHost()
    foreign_host = native.ClientHost()
    running = submit(host, init_request(tmp_path, "req_running"))
    foreign = submit(foreign_host, init_request(tmp_path, "req_foreign"))
    wait_until(lambda: started(native, running) and started(native, foreign), 5, "both starting")

    def refused(operation_id: str) -> str | None:
        with pytest.raises(RuntimeError) as failure:
            host.cancel_operation(operation_id)
        return error_code(failure.value)

    assert refused("op_req_unknown") == "InvalidRequest"
    assert refused(foreign) == "InvalidRequest"
    # A non-network request, a merge status, never registers.
    from gwz.protocol.codec import encode_message
    from gwz.protocol.generated import MergeOp, MergeRequest
    from host_helpers import meta

    merge = MergeRequest(
        meta=meta(tmp_path, "req_host_merge_status"), op=MergeOp.status, source_ref=None, merge_id=None,
        mode=None, message=None, preserve=None, filesystem_strict=None, local_source_name=None,
        wait_seconds=None,
    )
    host.submit("merge", "MergeRequest", "MergeResponse", encode_message("MergeRequest", merge))
    assert refused("op_req_host_merge_status") == "InvalidRequest"

    # Nothing was cancelled: both end on their missing sources.
    for native_result in (result(native, running), result(native, foreign)):
        assert [error.code for error in native_result.errors] != [GwzErrorCode.cancelled]
    assert refused(running) == "InvalidRequest"


def test_close_cancels_waiting_operations_refuses_new_ones_and_joins_the_running(
    native, held, tmp_path: Path
) -> None:
    host = native.ClientHost()
    running = [submit(host, init_request(tmp_path, f"req_closing_{i}")) for i in range(LIMIT)]
    wait_until(lambda: all(started(native, op) for op in running), 5, "eight operations starting")
    waiting = submit(host, init_request(tmp_path, "req_closing_waiting"))
    begun = time.monotonic()
    report = host.close()
    closed = time.monotonic() - begun
    print(f"close joined {LIMIT} running operations in {closed:.2f} s")
    # It joined the eight within the bound: none outlived it, so its report
    # counts no pending work. No peer cleanup is confirmed in 1.1.0.
    assert closed < CLEANUP_BOUND
    assert report == (0, False)
    assert [error.code for error in result(native, waiting).errors] == [GwzErrorCode.cancelled]
    assert all(native.try_operation_result(op) is not None for op in running)
    with pytest.raises(RuntimeError) as refused:
        submit(host, init_request(tmp_path, "req_after_close"))
    assert error_code(refused.value) == "InvalidRequest"
    assert host.close() == report


def test_close_with_a_failing_operation_in_flight_returns_within_the_bound(
    native, monkeypatch, tmp_path: Path
) -> None:
    """No wait holds the GIL (design §2.6): the failing operation ends while
    close waits, so close returns as soon as it ends, not at the bound with
    the operation unfinished."""
    monkeypatch.setenv(DELAY_VARIABLE, "1000")
    host = native.ClientHost()
    failing = submit(host, init_request(tmp_path, "req_failing"))
    wait_until(lambda: started(native, failing), 5, "the failing operation starting")
    begun = time.monotonic()
    report = host.close()
    closed = time.monotonic() - begun
    print(f"close joined a failing operation in {closed:.2f} s")
    assert closed < CLEANUP_BOUND - 1
    assert report == (0, False), "an operation that outlived the bound would count one pending job"
    outcome = result(native, failing)
    assert outcome.aggregate_status is AggregateStatus.failed
    assert [error.code for error in outcome.errors] != [GwzErrorCode.cancelled]


EXIT_CHILD = textwrap.dedent(
    """
    import os, sys, time
    from pathlib import Path

    sys.path.insert(0, sys.argv[2])
    from host_helpers import init_request, started, submit, wait_until
    from gwz import Client
    from gwz.bridge import NativeCoreBridge
    import gwz._gwz_core as native

    base = Path(sys.argv[1])
    client = Client(root=base, bridge=NativeCoreBridge(native=native))
    host = client.bridge._host
    operation = submit(host, init_request(base, "req_at_exit"))
    wait_until(lambda: started(native, operation), 5, "the operation starting")
    print("exiting", flush=True)
    # The interpreter exits with the operation running and the client open:
    # its host's exit callback closes it.
    """
)


def run_exit_child(
    tmp_path: Path, hold_ms: int, script: str = EXIT_CHILD, *args: str
) -> tuple[subprocess.CompletedProcess[str], float]:
    environment = {**os.environ, DELAY_VARIABLE: str(hold_ms)}
    tests = str(Path(__file__).resolve().parent)
    begun = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path), tests, *args],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    stdout, stderr = process.communicate(timeout=60)
    elapsed = time.monotonic() - begun
    child = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    # Nothing the child started outlives it: its session's process group is
    # empty.
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return child, elapsed
    os.killpg(process.pid, signal.SIGKILL)
    raise AssertionError("a process the child started outlived it")


def test_interpreter_exit_joins_a_running_operation(native, tmp_path: Path) -> None:
    child, elapsed = run_exit_child(tmp_path, 1500)
    print(f"exit with a 1.5 s operation running took {elapsed:.2f} s")
    assert child.returncode == 0, child.stderr
    assert child.stdout.split() == ["exiting"]
    assert elapsed < CLEANUP_BOUND + 5


def test_interpreter_exit_does_not_wait_past_the_bound_for_an_operation_that_outlives_it(
    native, tmp_path: Path
) -> None:
    child, elapsed = run_exit_child(tmp_path, 12_000)
    print(f"exit with a 12 s operation running took {elapsed:.2f} s")
    assert child.returncode == 0, child.stderr
    assert child.stdout.split() == ["exiting"]
    # The exit callback waits out the bound, then finalization goes on
    # without the operation, which is still asleep.
    assert CLEANUP_BOUND - 0.5 < elapsed < 12


FAILS_AFTER_EXIT_CHILD = textwrap.dedent(
    """
    import os, sys, time
    from pathlib import Path

    sys.path.insert(0, sys.argv[2])
    from host_helpers import DELAY_VARIABLE, call, init_request, started, submit, wait_until
    import gwz._gwz_core as native

    class Finalizer:
        # Collected when finalization clears this module, after the host's
        # exit callback has waited out its bound and the interpreter has
        # begun to finalize. It waits for the operation's outcome with the
        # GIL released, so the operation's thread could take it.

        def __init__(self, operation):
            self.operation = operation
            self.try_result = native.try_operation_result
            self.sleep = time.sleep
            self.clock = time.monotonic
            self.write = os.write

        def __del__(self):
            deadline = self.clock() + 8
            outcome = self.try_result(self.operation)
            while outcome is None and self.clock() < deadline:
                self.sleep(0.05)
                outcome = self.try_result(self.operation)
            line = "unrecorded" if outcome is None else "recorded " + bytes(outcome).hex()
            self.write(1, (line + "\\n").encode())

    base = Path(sys.argv[1])
    host = native.ClientHost()
    if sys.argv[3] == "after-an-earlier-failure":
        # An operation that fails while the interpreter runs builds its error
        # on a thread of its own. Once one has, PyO3 takes the interpreter
        # as initialized, and a later attach goes straight to CPython.
        hold = os.environ.pop(DELAY_VARIABLE)
        try:
            call(host, init_request(base, "req_fails_before_exit"))
        except RuntimeError:
            pass
        os.environ[DELAY_VARIABLE] = hold
    operation = submit(host, init_request(base, "req_fails_after_exit"))
    wait_until(lambda: started(native, operation), 5, "the operation starting")
    finalizer = Finalizer(operation)
    print("exiting", flush=True)
    """
)


@pytest.mark.parametrize("history", ["first-failure", "after-an-earlier-failure"])
def test_an_operation_that_fails_after_the_exit_bound_records_its_outcome_without_the_interpreter(
    native, tmp_path: Path, history: str
) -> None:
    """Design §2.6: an operation that outlives the bound at exit records its
    outcome without attaching to the interpreter. This one fails about a
    second after the exit callback gives up on it, while the interpreter
    finalizes. If building its error attaches, the interpreter, which
    reports itself uninitialized by then, is touched anyway: PyO3 panics on
    the process's first attach, and after an earlier one CPython ends the
    thread there (before 3.14; 3.14 parks it), so its outcome is never
    recorded."""
    child, elapsed = run_exit_child(
        tmp_path, int((CLEANUP_BOUND + 1) * 1000), FAILS_AFTER_EXIT_CHILD, history
    )
    print(f"exit with an operation failing after the bound took {elapsed:.2f} s")
    assert child.returncode == 0, child.stderr
    lines = child.stdout.split("\n")
    assert lines[0] == "exiting", child.stdout
    state, _, payload = lines[1].partition(" ")
    assert state == "recorded", (
        "the operation's thread ended when its failure attached to the finalizing interpreter"
    )
    outcome = decode_message("OperationResult", bytes.fromhex(payload))
    assert outcome.aggregate_status is AggregateStatus.failed
    assert [error.code for error in outcome.errors] == [GwzErrorCode.internal_error]
    assert "closed at interpreter exit" in outcome.errors[0].message
