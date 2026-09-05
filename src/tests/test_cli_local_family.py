"""LCM1.0c (lane CP): the Python CLI surface for the local clone family.

Every request assertion below is one row of the design's §7 CLI -> live
message table (`dev-docs/GwzLocalCloneDesign.md`). Core refuses every
local-family operation with `unsupported_operation` at this checkpoint, so
the presentation cases pin how a typed refusal reaches a person and a
machine reader; nothing here needs the native bridge.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from gwz import cli, cli_local
from gwz.cli import build_parser
from gwz.cli_shared import (
    CliUsageError,
    CommandContext,
    CommandRegistry,
    meta_kwargs,
    validate_args,
)
from gwz.client import Client
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    CloneLocalWorkspaceRequest,
    CloneLocalWorkspaceResponse,
    CloneWorkspaceResponse,
    EventKind,
    GwzError as GwzErrorDetail,
    GwzErrorCode,
    LocalCloneMode,
    LocalFamilyOp,
    LocalFamilyRequest,
    LocalFamilyResponse,
    MergeOp,
    MergeOperationState,
    MergeParticipantCounts,
    MergeRequest,
    MergeResponse,
    OperationEvent,
    OperationResult,
    PullHeadResponse,
    PushRequest,
    PushResponse,
    RequestMeta,
    ResponseEnvelope,
    ResponseMeta,
    Severity,
    SyncBehavior,
)


RESPONSE_TYPES: dict[str, type[Any]] = {
    "CloneLocalWorkspaceResponse": CloneLocalWorkspaceResponse,
    "LocalFamilyResponse": LocalFamilyResponse,
    "MergeResponse": MergeResponse,
    "PullHeadResponse": PullHeadResponse,
    "PushResponse": PushResponse,
}

RESPONSE_EXTRAS: dict[type[Any], dict[str, Any]] = {
    # LCM1.0c follow-up 2 (operator ruling 2026-09-05): the `gwz local list`
    # payload; empty for every other op and whenever the envelope carries an
    # error (design §7). The list rendering of a non-empty payload is lane
    # CP's.
    LocalFamilyResponse: {"members": []},
    MergeResponse: {
        "merge_id": None,
        "state": MergeOperationState.completed,
        "open": False,
        "participant_counts": MergeParticipantCounts(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        "repos": [],
        "operation_drift": [],
        "preservation": None,
        "publication_step": None,
        "record": None,
        "crash_recovery": None,
    },
}


def envelope(
    *,
    status: AggregateStatus = AggregateStatus.ok,
    message: str | None = "ok",
    errors: list[GwzErrorDetail] | None = None,
) -> ResponseEnvelope:
    return ResponseEnvelope(
        meta=ResponseMeta(
            request_id="req_local",
            schema_version="gwz.protocol/v0",
            action=ActionKind.local_family,
            aggregate_status=status,
            operation_id=None,
            message=message,
            attribution=None,
        ),
        members=[],
        errors=list(errors or []),
    )


def refusal(code: GwzErrorCode, message: str) -> GwzErrorDetail:
    return GwzErrorDetail(
        code=code,
        message=message,
        member_id=None,
        member_path=None,
        detail=None,
        target_kind=None,
        record_context=None,
    )


class RecordingBridge:
    """Captures the exact protocol request each argv row builds."""

    def __init__(
        self,
        *,
        status: AggregateStatus = AggregateStatus.ok,
        message: str | None = "ok",
        errors: list[GwzErrorDetail] | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, str, Any]] = []
        self.status = status
        self.message = message
        self.errors = errors

    async def call(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request: Any,
    ) -> Any:
        self.calls.append((method, request_message, response_message, request))
        response_type = RESPONSE_TYPES[response_message]
        return response_type(
            response=envelope(
                status=self.status, message=self.message, errors=self.errors
            ),
            **RESPONSE_EXTRAS.get(response_type, {}),
        )

    def subscribe_events(self, operation_id: str) -> AsyncIterator[Any]:
        async def _empty() -> AsyncIterator[Any]:
            if False:  # pragma: no cover - an empty async iterator
                yield None

        return _empty()

    async def operation_result(self, operation_id: str) -> OperationResult:
        return OperationResult(
            operation_id=operation_id,
            request_id="req_local",
            action=ActionKind.local_family,
            aggregate_status=self.status,
            started_at_ms=0,
            finished_at_ms=1,
            members=[],
            errors=list(self.errors or []),
            attribution=None,
        )


def run_cli(argv: list[str], bridge: RecordingBridge) -> Any:
    """argv -> parser -> command handler -> real Client -> recorded request."""

    args = build_parser().parse_args(argv)
    validate_args(args)
    context = CommandContext(
        args=args,
        client=Client(bridge=bridge),
        meta=meta_kwargs(args),
    )
    return asyncio.run(args.command_handler(context))


def sole_request(bridge: RecordingBridge) -> Any:
    assert len(bridge.calls) == 1
    return bridge.calls[0][3]


def reject(argv: list[str]) -> str:
    """Run one argv row and return the CLI usage refusal it raises."""

    bridge = RecordingBridge()
    with pytest.raises(CliUsageError) as raised:
        run_cli(argv, bridge)
    assert bridge.calls == [], "a CLI refusal must precede any request"
    return str(raised.value)


# --------------------------------------------------------------------------
# Design §7 table: `gwz clone --local ...` -> CloneLocalWorkspaceRequest
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,name,dest,mode,branch",
    [
        (
            ["clone", "--local", "--name", "A", "../gwz-dev-A"],
            "A",
            "../gwz-dev-A",
            LocalCloneMode.verbatim,
            None,
        ),
        (
            ["clone", "--local", "--clean", "-b", "lane/x", "--name", "C", "dest"],
            "C",
            "dest",
            LocalCloneMode.clean,
            "lane/x",
        ),
        (
            ["clone", "--local", "--bare", "--name", "hub", "dest"],
            "hub",
            "dest",
            LocalCloneMode.bare,
            None,
        ),
        (
            ["clone", "--local", "--name", "A"],
            "A",
            None,
            LocalCloneMode.verbatim,
            None,
        ),
        (
            ["clone", "--local", "--verbatim", "--name", "A", "dest"],
            "A",
            "dest",
            LocalCloneMode.verbatim,
            None,
        ),
        (
            ["clone", "--local", "--bare", "-b", "lane/x", "--name", "hub", "dest"],
            "hub",
            "dest",
            LocalCloneMode.bare,
            "lane/x",
        ),
    ],
)
def test_clone_local_builds_the_table_request(
    argv: list[str],
    name: str,
    dest: str | None,
    mode: LocalCloneMode,
    branch: str | None,
) -> None:
    bridge = RecordingBridge()

    run_cli(argv, bridge)

    method, request_message, response_message, request = bridge.calls[0]
    assert (method, request_message, response_message) == (
        "clone_local_workspace",
        "CloneLocalWorkspaceRequest",
        "CloneLocalWorkspaceResponse",
    )
    assert isinstance(request, CloneLocalWorkspaceRequest)
    assert (request.name, request.dest, request.mode, request.branch) == (
        name,
        dest,
        mode,
        branch,
    )
    assert request.meta.schema_version == "gwz.protocol/v0"
    assert request.meta.dry_run is None


def test_clone_local_passes_dry_run_through_for_core_to_refuse() -> None:
    # Design §6.2: v0 local create refuses unsupported dry-run in core, before
    # a family lock file or any reservation. The CLI does not pre-empt it.
    bridge = RecordingBridge()

    run_cli(["--dry-run", "clone", "--local", "--name", "A"], bridge)

    assert sole_request(bridge).meta.dry_run is True


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["clone", "--local"], "--name"),
        (["clone", "--local", "--name", ""], "--name"),
        (["clone", "--local", "--verbatim", "--clean", "--name", "A"], "mutually exclusive"),
        (["clone", "--local", "--verbatim", "--bare", "--name", "A"], "mutually exclusive"),
        (["clone", "--local", "-b", "lane/x", "--name", "A"], "--clean or --bare"),
        (["clone", "--local", "--from", "A", "--name", "B"], "not yet supported"),
        (["clone", "--local", "--name", "A", "dest", "extra"], "one destination"),
        (["clone", "--name", "A"], "--local"),
        (["clone", "--clean", "https://example.invalid/ws.git"], "--local"),
        (["clone", "--bare", "https://example.invalid/ws.git"], "--local"),
        (["clone", "--verbatim", "https://example.invalid/ws.git"], "--local"),
        (["clone", "-b", "lane/x", "https://example.invalid/ws.git"], "--local"),
        (["clone", "--from", "A", "https://example.invalid/ws.git"], "--local"),
        (["clone"], "requires a workspace URL"),
    ],
)
def test_clone_local_flag_validation_precedes_any_request(
    argv: list[str], expected: str
) -> None:
    assert expected in reject(argv)


# --------------------------------------------------------------------------
# Design §7 table: `gwz local ...` -> LocalFamilyRequest
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,op,name,keep,hazards",
    [
        (["local", "list"], LocalFamilyOp.list, None, None, []),
        (["local", "dispose", "C"], LocalFamilyOp.dispose, "C", None, []),
        (["local", "dispose", "C", "--keep"], LocalFamilyOp.dispose, "C", True, []),
        (
            ["local", "dispose", "C", "--force", "unpreserved-history"],
            LocalFamilyOp.dispose,
            "C",
            None,
            ["unpreserved-history"],
        ),
        (
            [
                "local",
                "dispose",
                "C",
                "--force",
                "open-merge,dirty,unpreserved-history",
            ],
            LocalFamilyOp.dispose,
            "C",
            None,
            ["open-merge", "dirty", "unpreserved-history"],
        ),
        (["local", "disband"], LocalFamilyOp.disband, None, None, []),
    ],
)
def test_local_family_builds_the_table_request(
    argv: list[str],
    op: LocalFamilyOp,
    name: str | None,
    keep: bool | None,
    hazards: list[str],
) -> None:
    bridge = RecordingBridge()

    run_cli(argv, bridge)

    method, request_message, response_message, request = bridge.calls[0]
    assert (method, request_message, response_message) == (
        "local_family",
        "LocalFamilyRequest",
        "LocalFamilyResponse",
    )
    assert isinstance(request, LocalFamilyRequest)
    assert (request.op, request.name, request.keep, request.force_hazards) == (
        op,
        name,
        keep,
        hazards,
    )


def test_local_family_passes_dry_run_through_for_core_to_refuse() -> None:
    bridge = RecordingBridge()

    run_cli(["--dry-run", "local", "list"], bridge)

    assert sole_request(bridge).meta.dry_run is True


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["local", "dispose", "C", "--force"], "hazard"),
        (["--force", "local", "dispose", "C"], "hazard"),
        (["local", "dispose", "C", "--force", ""], "hazard"),
        (["local", "dispose", "C", "--force", "dirty,"], "hazard"),
        (["local", "dispose", "C", "--force", ","], "hazard"),
        (["local", "dispose", "C", "--keep", "--force", "dirty"], "--keep"),
    ],
)
def test_local_dispose_force_validation_precedes_any_request(
    argv: list[str], expected: str
) -> None:
    assert expected in reject(argv)


def test_local_dispose_passes_unknown_hazard_names_to_core() -> None:
    # Design §7: the CLI rejects a bare --force; core owns unknown hazard names.
    bridge = RecordingBridge()

    run_cli(["local", "dispose", "C", "--force", "sunspots"], bridge)

    assert sole_request(bridge).force_hazards == ["sunspots"]


@pytest.mark.parametrize(
    "argv",
    [
        ["local"],
        ["local", "dispose"],
        ["local", "bogus"],
        ["local", "list", "--keep"],
        ["local", "disband", "--force", "dirty"],
    ],
)
def test_local_rejects_unsupported_shapes_at_parse(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        build_parser().parse_args(argv)

    assert raised.value.code != 0


# --------------------------------------------------------------------------
# Design §7 table: `gwz merge --remote <name> [<ref>]` -> MergeRequest
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,source_ref,local_source_name",
    [
        (["--json", "merge", "feature/x"], "feature/x", None),
        (["--json", "merge", "A"], "A", None),
        (["--json", "merge", "--remote", "A"], None, "A"),
        (["--json", "merge", "--remote", "C", "lane/agent-17"], "lane/agent-17", "C"),
        (["--json", "merge", "--remote", "origin"], None, "origin"),
        (["--json", "--remote", "A", "merge"], None, "A"),
    ],
)
def test_merge_remote_is_the_family_selector_not_a_policy_remote(
    argv: list[str], source_ref: str | None, local_source_name: str | None
) -> None:
    bridge = RecordingBridge()

    run_cli(argv, bridge)

    method, request_message, _response_message, request = bridge.calls[0]
    assert (method, request_message) == ("merge", "MergeRequest")
    assert isinstance(request, MergeRequest)
    assert request.op is MergeOp.start
    assert request.source_ref == source_ref
    assert request.local_source_name == local_source_name
    # The family binding is a request field, never OperationPolicy.remote.
    policy_remote = getattr(request.meta.policy, "remote", None)
    assert policy_remote is None


@pytest.mark.parametrize(
    "lifecycle", [["--continue"], ["--abort"], ["--status"], ["--gc"]]
)
def test_merge_remote_is_start_only(lifecycle: list[str]) -> None:
    assert "start" in reject(["merge", "--remote", "A", *lifecycle])


def test_merge_help_documents_the_family_selector() -> None:
    subparsers = next(
        action
        for action in build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    )

    help_text = subparsers.choices["merge"].format_help()

    assert "local clone family member" in help_text
    assert "git remote name" not in help_text


def test_family_merge_passes_dry_run_through_for_core_to_refuse() -> None:
    # Design §6.2: an invalid or unsupported family start refuses before any
    # import ref; the CLI does not pre-empt that refusal.
    bridge = RecordingBridge()

    run_cli(["--json", "--dry-run", "merge", "--remote", "A"], bridge)

    request = sole_request(bridge)
    assert request.local_source_name == "A"
    assert request.meta.dry_run is True


@pytest.mark.parametrize("remote", ["hub", "origin"])
def test_push_remote_keeps_its_git_and_family_token_unchanged(remote: str) -> None:
    bridge = RecordingBridge()

    run_cli(["push", "--remote", remote], bridge)

    request = sole_request(bridge)
    assert isinstance(request, PushRequest)
    assert request.remote == remote
    assert not hasattr(request, "local_source_name")


@pytest.mark.parametrize(
    "argv,remote,sync",
    [
        (["pull", "--head", "--remote", "A"], "A", None),
        (["pull", "--head", "--remote", "origin"], "origin", None),
        (
            ["--sync", "ff-only", "pull", "--head", "--remote", "root"],
            "root",
            SyncBehavior.ff_only,
        ),
    ],
)
def test_pull_head_remote_stays_an_operation_policy_binding(
    argv: list[str], remote: str, sync: SyncBehavior | None
) -> None:
    bridge = RecordingBridge()

    run_cli(argv, bridge)

    request = sole_request(bridge)
    assert request.meta.policy is not None
    assert request.meta.policy.remote == remote
    assert request.meta.policy.sync is sync


# --------------------------------------------------------------------------
# Presentation of the typed refusals core returns today
# --------------------------------------------------------------------------


REFUSALS = {
    "unsupported_operation": (
        GwzErrorCode.unsupported_operation,
        "local clone family: verbatim create is not implemented",
        "UnsupportedOperation",
    ),
    "invalid_request": (
        GwzErrorCode.invalid_request,
        "local family request name must not be empty",
        "InvalidRequest",
    ),
    "workspace_not_found": (
        GwzErrorCode.workspace_not_found,
        "no workspace root above the current directory",
        "WorkspaceNotFound",
    ),
}


def refusing_client_factory(
    code: GwzErrorCode, message: str
) -> Any:
    bridge = RecordingBridge(
        status=AggregateStatus.failed,
        message=message,
        errors=[refusal(code, message)],
    )

    def factory(**kwargs: Any) -> Client:
        return Client(bridge=bridge)

    return factory


@pytest.mark.parametrize("kind", sorted(REFUSALS))
@pytest.mark.parametrize(
    "argv",
    [
        ["local", "list"],
        ["local", "dispose", "C"],
        ["local", "disband"],
        ["clone", "--local", "--name", "A"],
    ],
)
def test_human_presentation_of_core_refusals(
    kind: str,
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, message, _label = REFUSALS[kind]
    monkeypatch.setattr(cli, "Client", refusing_client_factory(code, message))

    exit_code = cli.main(argv)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert captured.err == f"gwz: {message}\n"


@pytest.mark.parametrize("kind", sorted(REFUSALS))
def test_json_presentation_of_core_refusals(
    kind: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, message, label = REFUSALS[kind]
    monkeypatch.setattr(cli, "Client", refusing_client_factory(code, message))

    exit_code = cli.main(["--json", "local", "list"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == ""
    assert json.loads(captured.out) == {
        "kind": "response",
        "meta": None,
        "members": [],
        "errors": [
            {
                "code": label,
                "message": message,
                "member_id": None,
                "member_path": None,
                "detail": None,
                "target_kind": None,
                "record_context": None,
            }
        ],
        "workspace_git_status": None,
    }


def test_cli_usage_refusals_exit_two_and_name_the_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli,
        "Client",
        refusing_client_factory(GwzErrorCode.unsupported_operation, "unreachable"),
    )

    exit_code = cli.main(["local", "dispose", "C", "--force"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err.startswith("gwz: ")
    assert "hazard" in captured.err


def test_ok_local_family_response_renders_its_envelope_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # §7 open question 2: LocalFamilyResponse is envelope-only, so `local list`
    # renders the envelope message until the list payload is allocated.
    bridge = RecordingBridge(message="family: root, A")
    monkeypatch.setattr(cli, "Client", lambda **kwargs: Client(bridge=bridge))

    exit_code = cli.main(["local", "list"])

    assert exit_code == 0
    assert capsys.readouterr().out == "family: root, A\n"


# --------------------------------------------------------------------------
# The unrelated cli_local.py surface must keep working exactly as it did
# --------------------------------------------------------------------------


class CloneUrlClient:
    """The URL-clone slice of the client that cli_local.handle_clone drives."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def clone_workspace(self, url: str, target: str, **kwargs: Any) -> Any:
        self.calls.append(("clone_workspace", (url, target)))
        return CloneWorkspaceResponse(response=envelope())

    def clone_workspace_stream(self, url: str, target: str, **kwargs: Any) -> Any:
        self.calls.append(("clone_workspace_stream", (url, target)))

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
                merge_state=None,
                merge_member=None,
                artifact_path=None,
            )

        return _events()

    async def operation_result(self, operation_id: str) -> OperationResult:
        self.calls.append(("operation_result", (operation_id,)))
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


def run_with_client(argv: list[str], client: Any) -> Any:
    args = build_parser().parse_args(argv)
    validate_args(args)
    context = CommandContext(args=args, client=client, meta=meta_kwargs(args))
    return asyncio.run(args.command_handler(context))


def test_existing_forall_registration_is_untouched() -> None:
    registry = CommandRegistry()
    cli.register_commands(registry)
    specs = {spec.name: spec for spec in registry._commands}

    assert specs["forall"].handler is cli_local.handle_forall
    assert specs["forall"].configure is cli_local.configure_forall
    assert {"clone", "local", "merge"} <= set(specs)


def test_url_clone_still_streams_through_cli_local(capsys: pytest.CaptureFixture[str]) -> None:
    client = CloneUrlClient()

    response = run_with_client(["clone", "https://example.invalid/workspace.git"], client)

    assert isinstance(response, CloneWorkspaceResponse)
    assert client.calls == [
        ("clone_workspace_stream", ("https://example.invalid/workspace.git", "workspace")),
        ("operation_result", ("op_cli",)),
    ]
    capsys.readouterr()


def test_url_clone_machine_mode_and_explicit_target_are_unchanged() -> None:
    client = CloneUrlClient()

    run_with_client(
        ["--json", "clone", "git@example.invalid:org/ws.git", "work/demo"], client
    )

    assert client.calls == [
        ("clone_workspace", ("git@example.invalid:org/ws.git", "work/demo"))
    ]


def test_url_clone_keeps_its_own_dry_run_refusal() -> None:
    client = CloneUrlClient()

    with pytest.raises(CliUsageError, match="--dry-run is not supported for clone"):
        run_with_client(
            ["--dry-run", "clone", "https://example.invalid/workspace.git"], client
        )
