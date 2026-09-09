from __future__ import annotations

import asyncio
from pathlib import Path

from gwz.protocol.generated import (
    AddExistingRepoRequest,
    AddExistingRepoResponse,
    AggregateStatus,
    LsRequest,
    LsResponse,
    MemberStatus,
    PlannedAction,
)

from native_helpers import commit_file, create_git_repo, git, native_client


def test_native_serialized_caller_context_survives_executor_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    """Actual bridge calls must keep caller A after dispatch runs from B."""
    caller = tmp_path / "caller-a"
    executor = tmp_path / "executor-b"
    workspace = caller / "workspace"
    caller.mkdir()
    executor.mkdir()
    client = native_client(workspace)

    monkeypatch.chdir(caller)
    asyncio.run(client.create_workspace(workspace_id="ws_context"))
    repo = workspace / "outside-repo"
    caller_commit = create_git_repo(repo)
    misleading = executor / "workspace" / "outside-repo"
    create_git_repo(misleading)

    # Construct each request while caller A is current, then dispatch it from a
    # deliberately misleading B.  The request bytes, rather than B, define the
    # root and relative repository operand bases.
    ls_request = LsRequest(
        meta=client.meta(targets=["@root"]), include_unmaterialized=True
    )
    add_request = AddExistingRepoRequest(
        meta=client.meta(),
        repository_path="workspace/outside-repo",
        member_path=None,
        member_id=None,
        source_id=None,
    )
    monkeypatch.chdir(executor)

    listed = asyncio.run(
        client.bridge.call("ls", "LsRequest", "LsResponse", ls_request)
    )
    added = asyncio.run(
        client.bridge.call(
            "add_existing_repo",
            "AddExistingRepoRequest",
            "AddExistingRepoResponse",
            add_request,
        )
    )

    assert isinstance(listed, LsResponse)
    assert listed.members is not None
    assert listed.members[0].abspath == str(workspace)
    assert isinstance(added, AddExistingRepoResponse)
    assert added.response.members[0].member_path == "outside-repo"
    assert added.response.members[0].state is not None
    assert added.response.members[0].state.commit == caller_commit


def test_native_create_workspace_and_empty_status(tmp_path: Path) -> None:
    client = native_client(tmp_path)

    response = asyncio.run(client.create_workspace(workspace_id="ws_native"))

    assert response.response.meta.aggregate_status is AggregateStatus.ok
    assert (tmp_path / "gwz.conf" / "gwz.yml").is_file()
    assert (tmp_path / "gwz.conf" / "gwz.lock.yml").is_file()

    status = asyncio.run(client.status())

    assert status.response.meta.aggregate_status is AggregateStatus.ok
    assert status.response.members == []


def test_native_create_repo_status_and_repo_sync_dry_run(tmp_path: Path) -> None:
    client = native_client(tmp_path)
    asyncio.run(client.create_workspace(workspace_id="ws_native"))

    created = asyncio.run(
        client.create_repo("repos/app", member_id="mem_app", source_id="src_app")
    )

    assert created.response.meta.aggregate_status is AggregateStatus.ok
    created_member = created.response.members[0]
    assert created_member.member_id == "mem_app"
    assert created_member.member_path == "repos/app"
    assert created_member.status is MemberStatus.ok
    assert (tmp_path / "repos" / "app" / ".git").is_dir()

    status = asyncio.run(client.status())
    assert status.response.meta.aggregate_status is AggregateStatus.ok
    assert status.response.members[0].member_id == "mem_app"
    assert status.response.members[0].status is MemberStatus.ok

    repo = tmp_path / "repos" / "app"
    commit_file(repo, "README.md", "one\n", "initial")
    git(repo, "remote", "add", "origin", "https://example.invalid/org/app.git")

    synced = asyncio.run(client.repo_sync("repos/app", dry_run=True))

    assert synced.response.meta.aggregate_status is AggregateStatus.accepted
    synced_member = synced.response.members[0]
    assert synced_member.status is MemberStatus.planned
    assert synced_member.planned is not None


def test_native_add_existing_repo_records_git_state(tmp_path: Path) -> None:
    client = native_client(tmp_path)
    asyncio.run(client.create_workspace(workspace_id="ws_native"))
    repo = tmp_path / "local-repo"
    commit = create_git_repo(repo)

    added = asyncio.run(client.add_existing_repo(repo))

    assert added.response.meta.aggregate_status is AggregateStatus.ok
    member = added.response.members[0]
    assert member.status is MemberStatus.ok
    assert member.member_path == "local-repo"
    assert member.state is not None
    assert member.state.commit == commit

    status = asyncio.run(client.status(paths=["local-repo"]))

    assert status.response.meta.aggregate_status is AggregateStatus.ok
    assert status.response.members[0].member_path == "local-repo"


def test_native_repo_clone_detach_and_attach_routes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    commit = create_git_repo(source)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    client = native_client(workspace)
    asyncio.run(client.create_workspace(workspace_id="ws_native"))

    cloned = asyncio.run(
        client.clone_repo_member(
            str(source),
            "libs/shared",
            member_id="mem_shared",
            source_id="src_shared",
        )
    )
    detached = asyncio.run(client.detach_repo_member("libs/shared"))
    attached = asyncio.run(client.attach_repo_member("mem_shared"))

    assert cloned.response.meta.aggregate_status is AggregateStatus.ok
    assert cloned.response.members[0].member_id == "mem_shared"
    assert cloned.response.members[0].state is not None
    assert cloned.response.members[0].state.commit == commit
    assert detached.response.meta.aggregate_status is AggregateStatus.ok
    assert attached.response.meta.aggregate_status is AggregateStatus.ok
    assert attached.response.members[0].member_id == "mem_shared"
    assert attached.response.meta.message is not None
    assert "no snapshot or marker commit evidence was available" in attached.response.meta.message


def test_native_init_from_sources_dry_run_plans_without_cloning(tmp_path: Path) -> None:
    client = native_client(tmp_path)

    response = asyncio.run(
        client.init_from_sources(
            ["https://example.invalid/org/repo-a.git"],
            workspace_id="ws_native",
            dry_run=True,
        )
    )

    assert response.response.meta.aggregate_status is AggregateStatus.accepted
    member = response.response.members[0]
    assert member.status is MemberStatus.planned
    assert member.planned is not None
    assert member.planned.action is PlannedAction.clone
    assert not (tmp_path / "gwz.conf" / "gwz.yml").exists()


def test_native_transport_capabilities_are_typed_and_version_checked() -> None:
    import pytest
    from gwz.bridge import NativeCoreBridge
    from gwz.errors import GwzBridgeError
    from gwz.protocol.generated import TransportCapabilitiesRequest

    bridge = NativeCoreBridge()

    async def read(version):
        return await bridge.call(
            "transport_capabilities", "TransportCapabilitiesRequest",
            "TransportCapabilitiesResponse",
            TransportCapabilitiesRequest(schema_version=version),
        )

    response = asyncio.run(read("gwz.protocol/v0"))
    assert response.file_identity is True
    assert response.exact_agent_identity is False
    with pytest.raises(GwzBridgeError):
        asyncio.run(read("gwz.protocol/future"))


def test_native_local_identity_configuration_preserves_workspace_files(tmp_path: Path) -> None:
    from gwz.cli_render import render_response

    client = native_client(tmp_path)
    asyncio.run(client.create_workspace(workspace_id="ws_identity"))
    git(tmp_path, "remote", "add", "origin", "ssh://git@example.invalid/root")
    key = tmp_path / "key=one"
    key.write_text("fixture path only")
    before = {p.name: p.read_bytes() for p in (tmp_path / "gwz.conf").iterdir() if p.is_file()}
    response = asyncio.run(client.remote_identity("origin", key_path=str(key), targets=["@root"], dry_run=True))
    assert "planned" in render_response(response)
    assert "gwzSshIdentity" not in (tmp_path / ".git" / "config").read_text()
    response = asyncio.run(client.remote_identity("origin", key_path=str(key), targets=["@root"]))
    assert git(tmp_path, "config", "--local", "--get", "remote.origin.gwzSshIdentity") == str(key)
    assert response.identities[0].private_key_path == str(key)
    assert str(key) in render_response(response)
    assert '"identities"' in render_response(response, json_mode=True)
    key.unlink()
    response = asyncio.run(client.remote_identity("origin", targets=["@root"]))
    assert response.identities[0].private_key_path == str(key)
    asyncio.run(client.remote_identity("origin", unset=True, targets=["@root"]))
    assert "gwzSshIdentity" not in (tmp_path / ".git" / "config").read_text()
    assert {p.name: p.read_bytes() for p in (tmp_path / "gwz.conf").iterdir() if p.is_file()} == before


def test_native_transport_failure_retains_typed_observations(tmp_path: Path) -> None:
    import json
    import pytest
    from gwz.errors import GwzBridgeError
    from gwz.cli_render import render_error
    from gwz.protocol.generated import TransportOptions

    client = native_client(tmp_path)
    asyncio.run(client.create_workspace(workspace_id="ws_auth_failure"))
    git(tmp_path, "remote", "add", "origin", "ssh://git@127.0.0.1:1/unreachable")
    key = tmp_path / "key"
    key.write_text("fixture file; no server is contacted successfully")
    with pytest.raises(GwzBridgeError) as caught:
        asyncio.run(client.tag(op="list", remote="origin", targets=["@root"], transport=TransportOptions(default_identity=str(key), remote_identities=[])))
    meta = getattr(caught.value, "response_meta", None)
    assert meta is not None and meta.transport
    assert meta.transport[0].credential_offered is False
    assert meta.transport[0].authenticated is None
    value = json.loads(render_error(caught.value, json_mode=True))
    assert value["meta"]["transport"][0]["authenticated"] is None
    assert "authenticated=unknown" in render_error(caught.value, show_transport=True)


def test_native_timeout_is_configured_before_backend_use(tmp_path: Path) -> None:
    import subprocess
    import sys

    program = '''
import asyncio, sys
from gwz import Client
from gwz.errors import GwzBridgeError
async def main():
    client = Client(root=sys.argv[1])
    assert (await client.configure_transport_timeout(1)).server_timeout_ms == 1000
    await client.create_workspace(workspace_id="ws_timeout")
    await client.status()
    assert (await client.configure_transport_timeout(1)).server_timeout_ms == 1000
    try:
        await client.configure_transport_timeout(2)
    except GwzBridgeError as error:
        assert error.code == "UnsupportedOperation"
    else:
        raise AssertionError("late process-wide timeout change was accepted")
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
