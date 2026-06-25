from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from gwz import Client
from gwz.bridge import NativeCoreBridge
from gwz.errors import GwzBridgeError


def native_module():
    return pytest.importorskip("gwz._gwz_core")


def write_minimal_workspace(root: Path) -> None:
    conf = root / "gwz.conf"
    conf.mkdir()
    (conf / "gwz.yml").write_text(
        "\n".join(
            [
                "schema: gwz.workspace/v0",
                "workspace:",
                "  id: ws_native",
                "members:",
                "- id: mem_app",
                "  path: repos/app",
                "  type: git",
                "  source_id: src_app",
                "  active: true",
                "  remotes: []",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_native_module_health() -> None:
    native = native_module()

    assert native.health() == "ok"
    assert native.version()


def test_native_bridge_ls(tmp_path: Path) -> None:
    native = native_module()
    write_minimal_workspace(tmp_path)
    client = Client(root=tmp_path, bridge=NativeCoreBridge(native=native))

    response = asyncio.run(client.ls())

    assert response.members is not None
    assert [member.id for member in response.members] == ["mem_app"]
    assert response.members[0].path == "repos/app"
    assert not response.members[0].materialized


def test_native_bridge_routes_unimplemented_methods_explicitly(tmp_path: Path) -> None:
    native = native_module()
    write_minimal_workspace(tmp_path)
    client = Client(root=tmp_path, bridge=NativeCoreBridge(native=native))

    with pytest.raises(GwzBridgeError, match="not wired in gwz-py yet"):
        asyncio.run(client.status())
