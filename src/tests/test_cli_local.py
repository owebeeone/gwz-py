from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

from gwz.cli import build_parser
from gwz.cli_local import _forall_invocation, _run_forall
from gwz.cli_shared import CliUsageError, CommandContext, meta_kwargs, validate_args
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    CloneWorkspaceResponse,
    EventKind,
    ExecMode,
    LsResponse,
    MemberEntry,
    OperationEvent,
    OperationResult,
    RequestMeta,
    ResponseEnvelope,
    ResponseMeta,
    Severity,
    TargetKind,
)


class FakeClient:
    def __init__(self, members: list[MemberEntry], root: Path) -> None:
        self.members = members
        self.root = root
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    async def ls(self, **kwargs: Any) -> LsResponse:
        self.calls.append(("ls", (), kwargs))
        members = self.members
        targets = list(kwargs.get("targets") or ())
        if targets:
            selected = []
            for target in targets:
                selected.extend(
                    member
                    for member in members
                    if member.id == target or member.path == target
                )
            members = selected
        return LsResponse(response=response_envelope(ActionKind.ls), members=members)

    async def clone_workspace(self, url: str, target: str, **kwargs: Any) -> CloneWorkspaceResponse:
        self.calls.append(("clone_workspace", (url, target), kwargs))
        return CloneWorkspaceResponse(response=response_envelope(ActionKind.clone_workspace))

    def clone_workspace_stream(self, url: str, target: str, **kwargs: Any) -> Any:
        self.calls.append(("clone_workspace_stream", (url, target), kwargs))

        async def _events() -> Any:
            yield OperationEvent(
                operation_id="op_cli",
                request_id="req_cli",
                sequence=0,
                timestamp_ms=0,
                kind=EventKind.member_started,
                severity=Severity.info,
                member_id="workspace_root",
                member_path=target,
                message=None,
                member=None,
                error=None,
                attribution=None,
                progress=None,
                target_kind=None,
            )

        return _events()

    async def operation_result(self, operation_id: str) -> OperationResult:
        self.calls.append(("operation_result", (operation_id,), {}))
        return OperationResult(
            operation_id=operation_id,
            request_id="req_cli",
            action=ActionKind.clone_workspace,
            aggregate_status=AggregateStatus.ok,
            started_at_ms=0,
            finished_at_ms=1,
            members=[],
            errors=[],
            attribution=None,
        )

    def meta(self, **kwargs: Any) -> RequestMeta:
        return RequestMeta(
            request_id="req_cli",
            schema_version="gwz.protocol/v0",
            workspace=None,
            selection=None,
            policy=None,
            dry_run=kwargs.get("dry_run"),
            attribution=None,
        )


def response_envelope(action: ActionKind) -> ResponseEnvelope:
    return ResponseEnvelope(
        meta=ResponseMeta(
            request_id="req_cli",
            schema_version="gwz.protocol/v0",
            action=action,
            aggregate_status=AggregateStatus.ok,
            operation_id=None,
            message="ok",
            attribution=None,
        ),
        members=[],
        errors=[],
    )


def run_handler(argv: list[str], client: FakeClient) -> Any:
    args = build_parser().parse_args(argv)
    validate_args(args)
    context = CommandContext(args=args, client=client, meta=meta_kwargs(args))
    return asyncio.run(args.command_handler(context))


def member(id_: str, path: str, root: Path) -> MemberEntry:
    abspath = root / path
    abspath.mkdir(parents=True)
    return MemberEntry(
        id=id_,
        path=path,
        abspath=str(abspath),
        materialized=True,
        target_kind=None,
    )


def root_member(root: Path) -> MemberEntry:
    return MemberEntry(
        id="@root",
        path=".",
        abspath=str(root),
        materialized=True,
        target_kind=TargetKind.root,
    )


def test_forall_invocation_parses_argv_and_shell_forms() -> None:
    assert _forall_invocation(["repos/app", "--", "echo", "{@}"]) == (
        ["repos/app"],
        ExecMode.argv,
        ["echo", "{@}"],
    )
    assert _forall_invocation(["repos/app", "-c", "echo hi"]) == (
        ["repos/app"],
        ExecMode.shell,
        ["echo hi"],
    )


def test_forall_runs_filtered_member_command(tmp_path: Path) -> None:
    members = [member("mem_app", "repos/app", tmp_path), member("mem_lib", "repos/lib", tmp_path)]
    client = FakeClient(members, tmp_path)

    response = run_handler(
        [
            "forall",
            "--no-banner",
            "repos/app",
            "--",
            sys.executable,
            "-c",
            "import os, sys; sys.exit(0 if os.environ['GWZ_MEMBER_PATH'] == 'repos/app' else 7)",
        ],
        client,
    )

    assert response.response.meta.aggregate_status is AggregateStatus.ok
    assert [result.path for result in response.results] == ["repos/app"]
    assert response.results[0].exit_code == 0
    assert client.calls == [("ls", (), {"include_unmaterialized": False, "targets": ["repos/app"]})]


def test_forall_runs_positionally_selected_root_target(tmp_path: Path) -> None:
    client = FakeClient(
        [root_member(tmp_path), member("mem_app", "repos/app", tmp_path)],
        tmp_path,
    )

    response = run_handler(
        [
            "forall",
            "--no-banner",
            "@root",
            "--",
            sys.executable,
            "-c",
            "import os, sys; "
            "sys.exit(0 if os.environ['GWZ_TARGET_KIND'] == 'root' "
            "and os.environ['GWZ_MEMBER_PATH'] == '.' else 7)",
        ],
        client,
    )

    assert response.response.meta.aggregate_status is AggregateStatus.ok
    assert [result.path for result in response.results] == ["."]
    assert response.results[0].exit_code == 0
    assert client.calls == [
        ("ls", (), {"include_unmaterialized": False, "targets": ["@root"]})
    ]


def test_forall_stops_on_failure_unless_partial(tmp_path: Path) -> None:
    members = [member("mem_app", "repos/app", tmp_path), member("mem_lib", "repos/lib", tmp_path)]
    command = [sys.executable, "-c", "import sys; sys.exit(1)"]

    stopped = _run_forall(
        members=members,
        mode=ExecMode.argv,
        command=command,
        continue_on_fail=False,
        no_banner=True,
        root=str(tmp_path),
    )
    continued = _run_forall(
        members=members,
        mode=ExecMode.argv,
        command=command,
        continue_on_fail=True,
        no_banner=True,
        root=str(tmp_path),
    )

    assert [result.path for result in stopped] == ["repos/app"]
    assert [result.path for result in continued] == ["repos/app", "repos/lib"]


def test_forall_rejects_json_mode(tmp_path: Path) -> None:
    client = FakeClient([member("mem_app", "repos/app", tmp_path)], tmp_path)

    with pytest.raises(CliUsageError, match="forall does not support --json"):
        run_handler(["--json", "forall", "-c", "echo hi"], client)


def test_clone_streams_with_derived_target(tmp_path: Path) -> None:
    client = FakeClient([], tmp_path)

    response = run_handler(["clone", "https://example.invalid/workspace.git"], client)

    assert response.response.meta.action is ActionKind.clone_workspace
    assert client.calls == [
        ("clone_workspace_stream", ("https://example.invalid/workspace.git", "workspace"), {}),
        ("operation_result", ("op_cli",), {}),
    ]


def test_clone_uses_explicit_target_in_json_mode(tmp_path: Path) -> None:
    client = FakeClient([], tmp_path)

    response = run_handler(["--json", "clone", "git@example.invalid:org/ws.git", "work/demo"], client)

    assert response.response.meta.action is ActionKind.clone_workspace
    assert client.calls == [
        ("clone_workspace", ("git@example.invalid:org/ws.git", "work/demo"), {}),
    ]


def test_clone_rejects_dry_run(tmp_path: Path) -> None:
    client = FakeClient([], tmp_path)

    with pytest.raises(CliUsageError, match="--dry-run is not supported for clone"):
        run_handler(["--dry-run", "clone", "https://example.invalid/workspace.git"], client)
