"""Core takes the caller's directory only from the request (GwzCoreServerDesign §5).

The hosting process's own working directory is never consulted, so these tests
run the native core after deleting it.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from gwz.bridge import NativeCoreBridge
from gwz.errors import GwzBridgeError
from gwz.protocol.generated import InvocationContext, LsRequest, RequestMeta, WorkspaceRef
from native_helpers import native_module


def write_workspace(root: Path) -> None:
    conf = root / "gwz.conf"
    conf.mkdir(parents=True)
    (conf / "gwz.yml").write_text(
        "schema: gwz.workspace/v0\n"
        "workspace:\n"
        "  id: ws_caller_directory\n"
        "members:\n"
        "- id: mem_app\n"
        "  path: repos/app\n"
        "  type: git\n"
        "  source_id: src_app\n"
        "  active: true\n"
        "  remotes: []\n",
        encoding="utf-8",
    )


def ls_request(root: Path, caller_cwd: Path | None) -> LsRequest:
    return LsRequest(
        meta=RequestMeta(
            request_id="req_caller_directory",
            schema_version="gwz.protocol/v0",
            workspace=WorkspaceRef(root=str(root), workspace_id=None),
            selection=None,
            policy=None,
            dry_run=None,
            attribution=None,
            transport=None,
            invocation=None if caller_cwd is None else InvocationContext(caller_cwd=str(caller_cwd)),
        ),
        include_unmaterialized=True,
    )


def test_core_ignores_the_host_working_directory(tmp_path: Path) -> None:
    bridge = NativeCoreBridge(native=native_module())
    root = tmp_path / "workspace"
    write_workspace(root)
    deleted = tmp_path / "deleted"
    deleted.mkdir()
    previous = os.getcwd()
    os.chdir(deleted)
    try:
        deleted.rmdir()
        response = asyncio.run(bridge.call("ls", "LsRequest", "LsResponse", ls_request(root, root)))
    finally:
        os.chdir(previous)
    assert [member.id for member in response.members or []] == ["mem_app"]


def test_request_without_caller_directory_is_refused(tmp_path: Path) -> None:
    bridge = NativeCoreBridge(native=native_module())
    root = tmp_path / "workspace"
    write_workspace(root)
    with pytest.raises(GwzBridgeError, match="invocation context"):
        asyncio.run(bridge.call("ls", "LsRequest", "LsResponse", ls_request(root, None)))
