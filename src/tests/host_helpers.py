"""Helpers for driving a native ``ClientHost`` directly (gwz-py
dev-docs/GwzPyPerOperationTransportDesign.md §3)."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from gwz.client_helpers import sources
from gwz.protocol.codec import decode_message, encode_message
from gwz.protocol.generated import (
    EventKind,
    InitFromSourcesRequest,
    InvocationContext,
    OperationResult,
    RequestMeta,
    TransportOptions,
    WorkspaceRef,
)

# The extension's test hook: an operation's thread sleeps this long after its
# OperationStarted event, holding its slot (native/src/operations.rs).
DELAY_VARIABLE = "GWZ_PY_TEST_EVENT_DELAY_MS"

# gwz-core's cleanup bound, which bounds close, cancel and exit (design §2.6).
CLEANUP_BOUND = 5.0


def meta(
    base: Path,
    request_id: str,
    transport: TransportOptions | None = None,
    root: Path | None = None,
) -> RequestMeta:
    return RequestMeta(
        request_id=request_id,
        schema_version="gwz.protocol/v0",
        workspace=WorkspaceRef(root=str(root or base), workspace_id=None),
        selection=None,
        policy=None,
        dry_run=None,
        attribution=None,
        transport=transport,
        invocation=InvocationContext(caller_cwd=str(base)),
    )


def init_request(
    base: Path,
    request_id: str,
    source: str | None = None,
    transport: TransportOptions | None = None,
) -> InitFromSourcesRequest:
    """A network operation in gwz-core's transport scope. From the default,
    missing local source, it fails once its handler runs, after the delay
    hook, and opens no connection."""
    root = base / f"workspace-{request_id}"
    return InitFromSourcesRequest(
        meta=meta(base, request_id, transport, root),
        workspace_root=str(root),
        sources=sources([source or str(base / "missing-source")]),
        target=None,
        workspace_id=None,
    )


def submit(host: Any, request: InitFromSourcesRequest) -> str:
    """Submits `request` on `host` and returns its operation ID."""
    payload = host.submit(
        "init_from_sources",
        "InitFromSourcesRequest",
        "InitFromSourcesResponse",
        encode_message("InitFromSourcesRequest", request),
    )
    accepted = decode_message("InitFromSourcesResponse", bytes(payload))
    operation_id = accepted.response.meta.operation_id
    assert operation_id == f"op_{request.meta.request_id}"
    return operation_id


def call(host: Any, request: InitFromSourcesRequest) -> Any:
    payload = host.call(
        "init_from_sources",
        "InitFromSourcesRequest",
        "InitFromSourcesResponse",
        encode_message("InitFromSourcesRequest", request),
    )
    return decode_message("InitFromSourcesResponse", bytes(payload))


def started(native: Any, operation_id: str) -> bool:
    """Whether the operation has emitted OperationStarted: it holds a slot."""
    try:
        payloads, _complete = native.wait_events(operation_id, 0, 0)
    except RuntimeError:
        return False
    events = [decode_message("OperationEvent", bytes(payload)) for payload in payloads]
    return any(event.kind is EventKind.operation_started for event in events)


def result(native: Any, operation_id: str) -> OperationResult:
    return decode_message("OperationResult", bytes(native.operation_result(operation_id)))


def wait_until(condition: Callable[[], bool], timeout: float, what: str) -> float:
    """Polls `condition` until it holds; returns how long that took."""
    begun = time.monotonic()
    while not condition():
        if time.monotonic() - begun > timeout:
            raise AssertionError(f"{what} did not happen within {timeout} s")
        time.sleep(0.01)
    return time.monotonic() - begun


def error_code(error: BaseException) -> str | None:
    return getattr(error, "code", None)
