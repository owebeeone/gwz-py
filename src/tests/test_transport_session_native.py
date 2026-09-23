"""Candidate-only checks against the compiled PyO3 transport session.

Set GWZ_PY_NATIVE_MODULE to a candidate extension library to run these checks.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.machinery
import importlib.util
import os
import time

import pytest

from gwz.protocol.codec import encode_message
from gwz.protocol.generated import FetchRequest
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


def test_native_close_drains_reserved_worker_and_refuses_later_admission(native_module):
    session = native_module.TransportSession()
    session.reserve_operation("op_req_transport")
    with pytest.raises(RuntimeError, match="another network operation"):
        session.reserve_operation("op_other")
    with pytest.raises(RuntimeError, match="not owned"):
        session.cancel_operation("op_foreign")
    with ThreadPoolExecutor(max_workers=1) as workers:
        closing = workers.submit(session.close)
        deadline = time.monotonic() + 2
        while True:
            try:
                session.reserve_operation("op_other")
            except RuntimeError as exc:
                if "closed" in str(exc):
                    break
            if time.monotonic() >= deadline:
                pytest.fail("close did not stop admission")
            time.sleep(0.001)
        assert not closing.done()
        request = FetchRequest(meta=status_request().meta)
        payload = encode_message("FetchRequest", request)
        with pytest.raises(RuntimeError, match="closed"):
            session.call("fetch", "FetchRequest", "FetchResponse", payload)
        assert closing.result(timeout=2) == (0, False)
        assert session.close() == (0, False)
