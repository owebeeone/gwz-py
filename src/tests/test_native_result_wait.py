"""A native result wait must not hold the GIL: a failing worker needs it."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from native_helpers import native_module

# The scenario runs in a child process. A wait that held the GIL would freeze
# every thread in the interpreter, including this test's own timeout.
CHILD = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    import gwz._gwz_core as native
    from gwz.client_helpers import sources
    from gwz.protocol.codec import decode_message, encode_message
    from gwz.protocol.generated import (
        InitFromSourcesRequest,
        InvocationContext,
        RequestMeta,
        WorkspaceRef,
    )

    base = Path(sys.argv[1])
    request = InitFromSourcesRequest(
        meta=RequestMeta(
            request_id="req_result_without_gil",
            schema_version="gwz.protocol/v0",
            workspace=WorkspaceRef(root=str(base), workspace_id=None),
            selection=None,
            policy=None,
            dry_run=None,
            attribution=None,
            transport=None,
            invocation=InvocationContext(caller_cwd=str(base)),
        ),
        workspace_root=str(base / "workspace"),
        sources=sources([str(base / "missing-source")]),
        target=None,
        workspace_id=None,
    )
    accepted = native.ClientHost().submit(
        "init_from_sources",
        "InitFromSourcesRequest",
        "InitFromSourcesResponse",
        encode_message("InitFromSourcesRequest", request),
    )
    response = decode_message("InitFromSourcesResponse", bytes(accepted))
    # The worker is held just after OperationStarted (GWZ_PY_TEST_EVENT_DELAY_MS),
    # so this wait starts before it fails, and its failure needs the GIL to build
    # the Python error.
    payload = native.operation_result(response.response.meta.operation_id)
    result = decode_message("OperationResult", bytes(payload))
    print(result.aggregate_status.name, result.errors[0].code.name)
    """
)


def test_result_wait_releases_the_gil_for_a_failing_worker(tmp_path: Path) -> None:
    native_module()
    environment = {**os.environ, "GWZ_PY_TEST_EVENT_DELAY_MS": "500"}
    try:
        child = subprocess.run(
            [sys.executable, "-c", CHILD, str(tmp_path)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("operation_result held the GIL while waiting; the failing worker deadlocked")
    assert child.returncode == 0, child.stderr
    assert child.stdout.split() == ["failed", "internal_error"]
