"""Candidate-only checks against the compiled PyO3 transport session.

Set GWZ_PY_NATIVE_MODULE to a candidate extension library to run these checks.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import asyncio
import importlib.machinery
import importlib.util
import os
import time
from threading import Event

import pytest
from taut.wire import cbor as wire_cbor

from gwz.protocol.codec import decode_message, encode_message
from gwz.bridge import NativeCoreBridge
from gwz.errors import GwzBridgeError
from gwz.protocol.generated import AggregateStatus, FetchRequest
from test_bridge_transport import status_request


@pytest.fixture
def native_module():
    path = os.environ.get("GWZ_PY_NATIVE_MODULE")
    if path is None:
        pytest.skip("candidate extension path was not provided")
    name = "gwz._gwz_core"
    loader = importlib.machinery.ExtensionFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    assert hasattr(module, "TransportSession")
    return module


def test_native_session_bounds_independent_reservations_and_closes_them(native_module):
    session = native_module.TransportSession()
    ids = []
    for index in range(64):
        ids.append(session.reserve_operation(f"req_{index}"))
    with pytest.raises(RuntimeError, match="ledger is full"):
        session.reserve_operation("sixty-fifth")
    with pytest.raises(RuntimeError, match="not owned"):
        session.cancel_operation("op_foreign")
    assert session.cancel_operation(ids[0]) == (0, False)
    session.release_operation(ids[0])
    ids[0] = session.reserve_operation("replacement")
    with ThreadPoolExecutor(max_workers=1) as workers:
        closing = workers.submit(session.close)
        deadline = time.monotonic() + 2
        while True:
            try:
                session.reserve_operation("other")
            except RuntimeError as exc:
                if "closed" in str(exc):
                    break
            if time.monotonic() >= deadline:
                pytest.fail("close did not stop admission")
            time.sleep(0.001)
        request = FetchRequest(meta=status_request().meta)
        payload = encode_message("FetchRequest", request)
        with pytest.raises(RuntimeError, match="closed"):
            session.call("fetch", "FetchRequest", "FetchResponse", payload)
        assert closing.result(timeout=2) == (0, False)
        assert session.close() == (0, False)
        with pytest.raises(RuntimeError, match="cancelled before admission"):
            session.operation_result(ids[1])


def test_native_operation_identity_is_per_session_and_request_ids_are_validated(native_module):
    a = native_module.TransportSession()
    b = native_module.TransportSession()
    a_id = a.reserve_operation("shared")
    b_id = b.reserve_operation("shared")
    assert a_id != b_id
    assert a.issued_operation(a_id)
    assert not a.issued_operation(b_id)
    with pytest.raises(RuntimeError, match="not owned"):
        a.cancel_operation(b_id)
    with pytest.raises(RuntimeError, match="not owned"):
        a.try_operation_result(b_id)
    with pytest.raises(RuntimeError, match="not owned"):
        a.try_operation_result(a_id.rsplit("_", 1)[0] + "_999")
    for invalid in ("", "x" * 129, "bad\nrequest"):
        with pytest.raises(RuntimeError, match="invalid"):
            a.reserve_operation(invalid)
    assert a.cancel_operation(a_id) == (0, False)
    assert b.cancel_operation(b_id) == (0, False)
    a.release_operation(a_id)
    assert a.issued_operation(a_id)
    with pytest.raises(RuntimeError, match="expired or been released"):
        a.try_operation_result(a_id)
    with pytest.raises(RuntimeError, match="expired or been released"):
        a.cancel_operation(a_id)


def test_native_submit_refuses_invalid_registration_before_endpoint(native_module, monkeypatch):
    session = native_module.TransportSession()
    meta = replace(status_request().meta, request_id="bad\nrequest")
    payload = encode_message("FetchRequest", FetchRequest(meta=meta))
    monkeypatch.delenv("HOME", raising=False)
    with pytest.raises(RuntimeError, match="invalid request_id"):
        session.submit("fetch", "FetchRequest", "FetchResponse", payload)
    assert session.close() == (0, False)


def test_preaccept_failure_is_retained_before_submit_returns(native_module, monkeypatch):
    session = native_module.TransportSession()
    operation_id = session.reserve_operation("request-without-home")
    meta = replace(status_request().meta, request_id="request-without-home")
    payload = encode_message("FetchRequest", FetchRequest(meta=meta))
    monkeypatch.delenv("HOME", raising=False)
    with pytest.raises(RuntimeError, match="endpoint HOME is unavailable"):
        session.submit("fetch", "FetchRequest", "FetchResponse", payload)
    result_bytes = session.try_operation_result(operation_id)
    assert result_bytes is not None
    result = decode_message("OperationResult", result_bytes)
    assert result.operation_id == operation_id
    assert result.aggregate_status is AggregateStatus.failed
    session.close()
    assert decode_message("OperationResult", session.operation_result(operation_id)) == result
    session.release_operation(operation_id)


@pytest.mark.parametrize("method", ["call", "submit"])
def test_cancelled_reservation_cannot_remint_without_explicit_token(native_module, method):
    session = native_module.TransportSession()
    operation_id = session.reserve_operation("cancelled-request")
    assert session.cancel_operation(operation_id) == (0, False)
    meta = replace(status_request().meta, request_id="cancelled-request")
    payload = encode_message("FetchRequest", FetchRequest(meta=meta))
    with pytest.raises(RuntimeError, match="cancelled before admission"):
        getattr(session, method)("fetch", "FetchRequest", "FetchResponse", payload)
    with pytest.raises(RuntimeError, match="cancelled before admission"):
        session.operation_result(operation_id)
    with pytest.raises(RuntimeError, match="cancelled before admission"):
        session.try_operation_result(operation_id)
    next_id = session.reserve_operation("next-request")
    assert int(next_id.rsplit("_", 1)[1]) == int(operation_id.rsplit("_", 1)[1]) + 1
    session.release_operation(operation_id)
    session.cancel_operation(next_id)
    session.close()


@pytest.mark.parametrize("method", ["call", "submit"])
@pytest.mark.parametrize("home_available", [False, True])
def test_explicit_cli_placement_refuses_before_endpoint_construction(
    native_module, monkeypatch, tmp_path, method, home_available,
):
    if home_available:
        monkeypatch.setenv("HOME", str(tmp_path))
    else:
        monkeypatch.delenv("HOME", raising=False)
    session = native_module.TransportSession()
    payload = wire_cbor.loads(encode_message("FetchRequest", FetchRequest(meta=status_request().meta)))
    payload[1][8] = {1: None, 2: [], 3: None, 4: 2, 5: None}
    with pytest.raises(RuntimeError, match="no CLI endpoint placement"):
        getattr(session, method)("fetch", "FetchRequest", "FetchResponse", wire_cbor.dumps(payload))
    session.close()


@pytest.mark.parametrize("method", ["call", "submit"])
def test_queued_native_call_keeps_issued_id_after_prestart_cancel(native_module, monkeypatch, method):
    monkeypatch.delenv("HOME", raising=False)

    async def run():
        blocked = Event()
        release = Event()
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=1) as workers:
            loop.set_default_executor(workers)

            def occupy():
                blocked.set()
                release.wait(timeout=2)

            blocker = loop.run_in_executor(None, occupy)
            assert blocked.wait(timeout=1)
            bridge = NativeCoreBridge(native=native_module)
            operation_id = bridge.issue_operation("req_transport")
            request = FetchRequest(meta=status_request().meta)
            task = asyncio.create_task(getattr(bridge, method)(
                "fetch", "FetchRequest", "FetchResponse", request,
            ))
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0.02)
            release.set()
            await blocker
            with pytest.raises(asyncio.CancelledError):
                await task
            assert bridge._session.issued_operation(operation_id)
            with pytest.raises(RuntimeError, match="cancelled before admission"):
                bridge._session.operation_result(operation_id)
            next_id = bridge.issue_operation("next")
            assert int(next_id.rsplit("_", 1)[1]) == int(operation_id.rsplit("_", 1)[1]) + 1
            await bridge.close()

    asyncio.run(run())


def test_implicit_preaccept_refusals_expose_releasable_ids(native_module, monkeypatch):
    monkeypatch.delenv("HOME", raising=False)

    async def run():
        bridge = NativeCoreBridge(native=native_module)
        for index in range(65):
            meta = replace(status_request().meta, request_id=f"refused-{index}")
            with pytest.raises(GwzBridgeError) as failure:
                await bridge.call("fetch", "FetchRequest", "FetchResponse", FetchRequest(meta=meta))
            operation_id = failure.value.operation_id
            assert operation_id is not None
            with pytest.raises(GwzBridgeError, match="HOME is unavailable"):
                await bridge.operation_result(operation_id)
            await bridge.release_operation(operation_id)
        await bridge.close()

    asyncio.run(run())
