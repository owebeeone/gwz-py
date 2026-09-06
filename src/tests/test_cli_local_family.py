"""LCM1.0c (lane CP): the Python CLI surface for the local clone family.

Every request assertion below is one row of the design's §7 CLI -> live
message table (`dev-docs/GwzLocalCloneDesign.md`), and the rows themselves
live in the one cross-driver parity fixture
`gwz-core/protocol/fixtures/cli_parity/local_family_cases.json` rather than
inline here: the Rust driver asserts against the same file, so a row added on
one side cannot silently go missing on the other. Neither driver keeps a copy
(operator ruling 1 of 2026-09-06, design §11 item 17).

Core still answers `unsupported_operation` for every local-family operation
(lane S is building the store), so the `gwz local list` rendering cases drive
constructed `LocalFamilyResponse` values, and one case keeps the current
refusal path pinned. Nothing here needs the native bridge.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from gwz import cli, cli_local, cli_local_family
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
    LocalFamilyMemberEntry,
    LocalFamilyRequest,
    LocalFamilyResponse,
    LocalMemberKind,
    LocalMemberState,
    LocalObservedState,
    MergeOperationState,
    MergeParticipantCounts,
    MergeRequest,
    MergeResponse,
    OperationEvent,
    OperationResult,
    PullHeadRequest,
    PullHeadResponse,
    PushRequest,
    PushResponse,
    RequestMeta,
    ResponseEnvelope,
    ResponseMeta,
    Severity,
)


#: The single cross-driver source (operator ruling 1 of 2026-09-06): the
#: fixture lives in `gwz-core`, beside the existing `merge_response.json`, and
#: both drivers read it from there. `scripts/check_protocol_drift.py` already
#: reaches the sibling checkout the same way.
PARITY_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "gwz-core"
    / "protocol"
    / "fixtures"
    / "cli_parity"
    / "local_family_cases.json"
)

#: A wheel-installed or standalone `gwz-py` checkout has no `gwz-core` sibling.
#: The parity rows are then unreadable, which is a gap in coverage, not a
#: failure of this driver -- so the rows skip, saying exactly what is missing.
FIXTURE_MISSING_REASON = (
    f"the cross-driver parity fixture is not in this checkout: {PARITY_FIXTURE}"
    " (it lives in the sibling gwz-core repository)"
)

#: Rows the fixture scopes away from this driver, with the reason each is not
#: assertable here. Pinned so that a row scoped `rust` later cannot quietly
#: become a hole in the Python driver's coverage.
ROWS_THIS_DRIVER_CANNOT_ASSERT = {
    "message_cases": {},
    "message_refusals": {
        # `dispose C dirty` is an unrecognized operand, so argparse exits
        # before `handle_local` runs: a parser exit, not the typed
        # `CliUsageError` this harness reads a message from. Refused before
        # encoding either way -- `test_local_rejects_unsupported_shapes_at_parse`
        # covers the parser exit.
        "local-dispose-hazards-without-force": "argparse exits before the handler",
    },
}


def parity_document() -> dict[str, Any]:
    return json.loads(PARITY_FIXTURE.read_text(encoding="utf-8"))


def parity_cases(key: str) -> list[Any]:
    """The fixture rows this driver is expected to satisfy.

    `drivers` scopes a row to the drivers that can express it (fixture
    `_schema`); a row this driver is not in is skipped, never asserted.
    """

    if not PARITY_FIXTURE.exists():
        return [
            pytest.param(
                None,
                id="parity-fixture-missing",
                marks=pytest.mark.skip(reason=FIXTURE_MISSING_REASON),
            )
        ]
    return [
        case
        for case in parity_document()[key]
        if "python" in case.get("drivers", ["python", "rust"])
    ]


def require_parity_fixture() -> dict[str, Any]:
    if not PARITY_FIXTURE.exists():
        pytest.skip(FIXTURE_MISSING_REASON)
    return parity_document()


def case_id(case: Any) -> str:
    return case["id"] if isinstance(case, dict) else "parity-fixture-missing"


def needles(case: dict[str, Any]) -> list[str]:
    """The substrings this driver's refusal message must contain.

    The shared `message_contains` plus `driver_message_contains.python`: the
    wording around a shared needle differs per driver, so each driver pins the
    rest of its own message beside the needles both must carry.
    """

    return [
        *case["message_contains"],
        *case.get("driver_message_contains", {}).get("python", []),
    ]


def resolve_field(request: Any, path: str) -> Any:
    """Resolve one fixture dotted path, through absent intermediates."""

    value: Any = request
    for part in path.split("."):
        if value is None:
            return None
        value = getattr(value, part)
    return value


def assert_field(request: Any, path: str, expected: Any) -> None:
    actual = resolve_field(request, path)
    if isinstance(expected, str) and hasattr(actual, "name"):
        actual = actual.name
    assert actual == expected, f"{path}: {actual!r} != {expected!r}"


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
    LocalFamilyResponse: {"members": [], "root_path": None},
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
    operation_id: str | None = None,
) -> ResponseEnvelope:
    return ResponseEnvelope(
        meta=ResponseMeta(
            request_id="req_local",
            schema_version="gwz.protocol/v0",
            action=ActionKind.local_family,
            aggregate_status=status,
            operation_id=operation_id,
            message=message,
            attribution=None,
        ),
        members=[],
        errors=list(errors or []),
    )


def refusal(
    code: GwzErrorCode, message: str, *, detail: str | None = None
) -> GwzErrorDetail:
    return GwzErrorDetail(
        code=code,
        message=message,
        member_id=None,
        member_path=None,
        detail=detail,
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


class SubmittingBridge(RecordingBridge):
    """A `RecordingBridge` that also serves the submitted-merge path.

    Human-mode `gwz merge` streams core's diagnostics, so it submits and then
    reads the retained response. The fixture holds the design's command lines
    byte-for-byte, without a `--json` that would route around that path, so
    the recorder has to serve it.
    """

    async def submit(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request: Any,
    ) -> Any:
        self.calls.append((method, request_message, response_message, request))
        response_type = RESPONSE_TYPES[response_message]
        self.submitted = response_type(
            response=envelope(
                status=self.status,
                message=self.message,
                errors=self.errors,
                operation_id="op_local",
            ),
            **RESPONSE_EXTRAS.get(response_type, {}),
        )
        return self.submitted

    async def merge_operation_response(self, operation_id: str) -> Any:
        return self.submitted


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
# Design §7 table, driven from the shared parity fixture
# --------------------------------------------------------------------------

#: The generated request type, bridge method and response message each
#: fixture `message` name routes to. The fixture names only the request
#: message; the routing is this driver's.
MESSAGE_ROUTES: dict[str, tuple[type[Any], str, str]] = {
    "CloneLocalWorkspaceRequest": (
        CloneLocalWorkspaceRequest,
        "clone_local_workspace",
        "CloneLocalWorkspaceResponse",
    ),
    "LocalFamilyRequest": (
        LocalFamilyRequest,
        "local_family",
        "LocalFamilyResponse",
    ),
    "MergeRequest": (MergeRequest, "merge", "MergeResponse"),
    "PullHeadRequest": (PullHeadRequest, "pull_head", "PullHeadResponse"),
    "PushRequest": (PushRequest, "push", "PushResponse"),
}


@pytest.mark.parametrize("case", parity_cases("message_cases"), ids=case_id)
def test_parity_fixture_message_cases_build_the_design_row_request(
    case: dict[str, Any],
) -> None:
    request_type, method, response_message = MESSAGE_ROUTES[case["message"]]
    assert request_type.__name__ == case["message"]
    bridge = SubmittingBridge()

    run_cli(case["argv"], bridge)

    call_method, request_message, call_response_message, request = bridge.calls[0]
    assert (call_method, request_message, call_response_message) == (
        method,
        case["message"],
        response_message,
    )
    assert isinstance(request, request_type)
    assert request.meta.schema_version == "gwz.protocol/v0"
    for path, expected in case["fields"].items():
        assert_field(request, path, expected)


@pytest.mark.parametrize("case", parity_cases("message_refusals"), ids=case_id)
def test_parity_fixture_refusals_precede_any_request(case: dict[str, Any]) -> None:
    assert case["refused_by"] == "cli"
    message = reject(case["argv"])

    for fragment in needles(case):
        assert fragment in message, f"{case['id']}: {fragment!r} not in {message!r}"


def test_parity_fixture_covers_every_design_row_message() -> None:
    """Every message the §7 table names has at least one fixture row."""

    require_parity_fixture()
    covered = {case["message"] for case in parity_cases("message_cases")}

    assert covered == set(MESSAGE_ROUTES)


@pytest.mark.parametrize("key", sorted(ROWS_THIS_DRIVER_CANNOT_ASSERT))
def test_parity_rows_scoped_away_from_this_driver_are_the_known_ones(key: str) -> None:
    """A row scoped away from Python is one this driver documents, not a hole.

    The fixture never deletes a case a driver cannot express -- it scopes it
    with `drivers` -- so the set that this harness silently drops has to stay
    the set whose reason is recorded above.
    """

    document = require_parity_fixture()

    scoped_away = {
        case["id"]
        for case in document[key]
        if "python" not in case.get("drivers", ["python", "rust"])
    }

    assert scoped_away == set(ROWS_THIS_DRIVER_CANNOT_ASSERT[key])


# --------------------------------------------------------------------------
# Refusals and shapes that are this driver's own, not shared parity rows
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["clone", "--local", "--name", "A", "dest", "extra"], "one destination"),
        # Ruling 4 of 2026-09-06: an empty --name refuses at the driver, before
        # a workspace discovery and a family read learn the same thing from
        # core's own shape check. A shared fixture row now, kept here too as
        # the cover for a checkout without the gwz-core sibling.
        (["clone", "--local", "--name", ""], "--name"),
        (["clone", "--name", "A"], "--local"),
        (["clone", "--clean", "https://example.invalid/ws.git"], "--local"),
        (["clone", "--bare", "https://example.invalid/ws.git"], "--local"),
        (["clone", "--verbatim", "https://example.invalid/ws.git"], "--local"),
        (["clone", "-b", "lane/x", "https://example.invalid/ws.git"], "--local"),
        (["clone"], "requires a workspace URL"),
    ],
)
def test_clone_local_flag_validation_precedes_any_request(
    argv: list[str], expected: str
) -> None:
    assert expected in reject(argv)


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


@pytest.mark.parametrize("lifecycle", [["--abort"], ["--status"], ["--gc"]])
def test_merge_remote_is_start_only(lifecycle: list[str]) -> None:
    # All four lifecycle spellings are shared fixture rows now. These stay as
    # this repository's own cover for them, because a checkout without the
    # gwz-core sibling skips every fixture row.
    assert "start" in reject(["merge", "--remote", "A", *lifecycle])


def test_merge_remote_may_precede_the_verb() -> None:
    # `--remote` is a global option, so it parses before `merge` too; the
    # family binding must survive that spelling. Not a design row, so it is
    # this driver's own case rather than a fixture one.
    bridge = SubmittingBridge()

    run_cli(["--remote", "A", "merge"], bridge)

    request = sole_request(bridge)
    assert request.local_source_name == "A"
    assert getattr(request.meta.policy, "remote", None) is None


def test_push_encodes_the_remote_token_once_and_has_no_family_field() -> None:
    # Operator ruling 4 of 2026-09-06 (design §7, §11 item 20): the push token
    # goes in `PushRequest.remote` and nowhere else -- `OperationPolicy.remote`
    # stays absent, so core's request-over-policy precedence never has two
    # tokens to reconcile. The shared fixture's two push rows pin the same
    # thing; this is the cover for a checkout without the gwz-core sibling.
    # `local_source_name` is merge's field, not push's: `push` reaches a family
    # member through the same `remote` token a git remote uses.
    bridge = RecordingBridge()

    run_cli(["push", "--remote", "hub"], bridge)

    request = sole_request(bridge)
    assert isinstance(request, PushRequest)
    assert request.remote == "hub"
    assert getattr(request.meta.policy, "remote", None) is None
    assert not hasattr(request, "local_source_name")


def test_merge_help_documents_the_family_selector() -> None:
    subparsers = next(
        action
        for action in build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    )

    help_text = subparsers.choices["merge"].format_help()

    assert "local clone family member" in help_text
    assert "git remote name" not in help_text


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
    # Design §7 / §11 item 13: the family-only merge miss. It is not folded
    # into missing_remote, and it is presented exactly as its neighbours are.
    "unknown_local": (
        GwzErrorCode.unknown_local,
        "merge --remote C: 'C' is not a ready local family member (incomplete)",
        "UnknownLocal",
    ),
    # LCM1.1 fix 1 (lane C, 2026-09-06): the four local-create outcomes that
    # were folded into unsupported_operation and io_error. Each is a typed
    # refusal like its neighbours -- the human line carries core's message
    # unedited and machine output the PascalCase label -- so a driver and an
    # operator can tell a design section 4.0 hazard from "not built yet",
    # and a stopped copy, a moved source or an incomplete destination from a
    # plain I/O failure.
    "unsupported_source_layout": (
        GwzErrorCode.unsupported_source_layout,
        "local clone `A` -> /Users/limbo/gwz-dev-A: inventory source failed: "
        "/Users/limbo/gwz-dev/app: unsupported layout: Alternates; "
        "nothing was reserved",
        "UnsupportedSourceLayout",
    ),
    "copy_failed": (
        GwzErrorCode.copy_failed,
        "local clone `A` -> /Users/limbo/gwz-dev-A: copy tree failed: copy failed "
        "at app/locked.txt: SourceUnreadable: Permission denied; effects: "
        "[RowAllocated, DestinationAllocated, ErrorRecorded]; the `creating` "
        "row `A` and /Users/limbo/gwz-dev-A are retained for inspection",
        "CopyFailed",
    ),
    "source_drift": (
        GwzErrorCode.source_drift,
        "local clone `A` -> /Users/limbo/gwz-dev-A: recheck source failed: source "
        "drift: repository @root changed; effects: [RowAllocated, "
        "DestinationAllocated, TreeCopied, DestinationGitInstalled, "
        "PointerInstalled, ErrorRecorded]; the `creating` row `A` and "
        "/Users/limbo/gwz-dev-A are retained for inspection",
        "SourceDrift",
    ),
    "destination_incomplete": (
        GwzErrorCode.destination_incomplete,
        "local clone `A` -> /Users/limbo/gwz-dev-A: check destination failed: "
        "destination is incomplete: mem_app: objects missing from the destination "
        "store (7 objects in the store, 3 roots): 3f6b4a59 (below 8ec04f1d); "
        "effects: [RowAllocated, DestinationAllocated, TreeCopied, "
        "DestinationGitInstalled, PointerInstalled, ErrorRecorded]",
        "DestinationIncomplete",
    ),
    # LCM1.2 (lane C, 2026-09-06): the two family-merge import outcomes. A
    # pairing set mismatch is refused before any fetch with nothing written;
    # an import that stopped before the engine names every retained import
    # ref. Presented like every other typed refusal.
    "pairing_mismatch": (
        GwzErrorCode.pairing_mismatch,
        "local family merge from `A`: import of HEAD from `A` as "
        "refs/gwz/local-imports/xfer_0123456789abcdef0123456789abcdef failed: "
        "import pairing is incomplete; unpaired: mem_extra; no import ref was "
        "created; nothing was written; the merge engine was not entered; a retry "
        "mints a fresh transfer id",
        "PairingMismatch",
    ),
    "import_incomplete": (
        GwzErrorCode.import_incomplete,
        "local family merge from `A`: import of HEAD from `A` as "
        "refs/gwz/local-imports/xfer_0123456789abcdef0123456789abcdef failed: "
        "mem_lib: transfer failed: /Users/limbo/gwz-dev/lib: failed to update "
        "refs; retained import refs (ordinary Git refs, never pruned by gwz): "
        "mem_app refs/gwz/local-imports/xfer_0123456789abcdef0123456789abcdef = "
        "2f44c2f5f825e785ae19c1b85bc358fb4e89d61d; the merge engine was not "
        "entered; a retry mints a fresh transfer id",
        "ImportIncomplete",
    ),
    # LCM2.1/LCM2.2 (lane C, 2026-09-06): the three ordinary-disposal
    # outcomes. A known hazard --force did not name, evidence that could not
    # be established (no force name waives it), and a removal that stopped
    # part-way (the row is `disposing`). Presented like every other typed
    # refusal: the message names every finding, the recovery and the effects.
    "unwaived_hazard": (
        GwzErrorCode.unwaived_hazard,
        "local dispose `B` at /Users/limbo/gwz-dev-B: unwaived hazard(s): "
        "`mem_app` <unpreserved-history>: 1 protected root(s) of mem_app are "
        "preserved whole in no surviving family repository: Head "
        "2f44c2f5f825e785ae19c1b85bc358fb4e89d61d; name each accepted loss "
        "with --force <hazard,...> to delete, or --keep to detach and retain "
        "every file; nothing was removed; effects: []",
        "UnwaivedHazard",
    ),
    "unknown_evidence": (
        GwzErrorCode.unknown_evidence,
        "local dispose `A` at /Users/limbo/gwz-dev-A: unknown evidence: "
        "UnsupportedEvidence: `@root`: OpenGwzStash has no waiver (1 gwz stash "
        "record(s) under /Users/limbo/gwz-dev-A/.gwz/stash/bundles: this build "
        "does not decode gwz stash coordination records); no force name waives "
        "unknown evidence: make it interpretable, or --keep to detach and "
        "retain every file; nothing was removed; effects: []",
        "UnknownEvidence",
    ),
    "disposal_incomplete": (
        GwzErrorCode.disposal_incomplete,
        "local dispose `A` at /Users/limbo/gwz-dev-A: removal stopped "
        "(/Users/limbo/gwz-dev-A/app/locked/held: Permission denied (os error "
        "13)); remaining: /Users/limbo/gwz-dev-A, "
        "/Users/limbo/gwz-dev-A/app/locked/held; the row is `disposing` and the "
        "remainder is retained for inspection; there is no replay and a repeat "
        "is refused: clean up by hand, then an explicit dispose removes the "
        "stale row, or --keep detaches the remainder; effects: [RowDisposing]",
        "DisposalIncomplete",
    ),
}


def refusing_client_factory(
    code: GwzErrorCode,
    message: str,
    *,
    detail: str | None = None,
    bridge_class: type[RecordingBridge] = RecordingBridge,
) -> Any:
    bridge = bridge_class(
        status=AggregateStatus.failed,
        message=message,
        errors=[refusal(code, message, detail=detail)],
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


def test_ok_local_family_response_without_members_renders_its_envelope_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `LocalFamilyResponse.members` is the list payload and is empty for every
    # other op (design §7), so dispose, disband -- and any list core could not
    # populate -- still render the envelope message.
    bridge = RecordingBridge(message="family: root, A")
    monkeypatch.setattr(cli, "Client", lambda **kwargs: Client(bridge=bridge))

    exit_code = cli.main(["local", "list"])

    assert exit_code == 0
    assert capsys.readouterr().out == "family: root, A\n"


def test_unknown_local_reaches_a_machine_reader_as_a_typed_refusal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # `gwz merge --remote origin` is family-only, so core answers
    # unknown_local (62) with the state detail rather than missing_remote.
    message = "merge --remote origin: 'origin' is reserved, not a family member"
    monkeypatch.setattr(
        cli,
        "Client",
        refusing_client_factory(
            GwzErrorCode.unknown_local, message, detail="reserved-name"
        ),
    )

    exit_code = cli.main(["--json", "merge", "--remote", "origin"])

    captured = capsys.readouterr()
    assert exit_code == 1
    payload = json.loads(captured.out)
    assert [error["code"] for error in payload["errors"]] == ["UnknownLocal"]
    assert payload["errors"][0]["message"] == message
    assert payload["errors"][0]["detail"] == "reserved-name"


def test_unknown_local_reaches_a_person_through_the_merge_renderer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    message = "merge --remote origin: 'origin' is reserved, not a family member"
    monkeypatch.setattr(
        cli,
        "Client",
        refusing_client_factory(
            GwzErrorCode.unknown_local, message, bridge_class=SubmittingBridge
        ),
    )

    exit_code = cli.main(["merge", "--remote", "origin"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert f"UnknownLocal: {message}" in captured.out


# --------------------------------------------------------------------------
# `gwz local list` rendering (design §8.1 and the §7 members payload)
# --------------------------------------------------------------------------


#: The root of the design §8.1 family, as `LocalFamilyResponse.root_path`
#: carries it: the index directory core observed, reached through the pointer
#: when the listing runs in a clone.
DESIGN_ROOT = "/Users/limbo/gwz-dev"


def member_entry(
    name: str,
    *,
    kind: LocalMemberKind = LocalMemberKind.checkout,
    recorded: LocalMemberState = LocalMemberState.ready,
    observed: LocalObservedState = LocalObservedState.ready,
    path: str | None = None,
    last_error: str | None = None,
) -> LocalFamilyMemberEntry:
    # `path` is root-relative on the wire (design §7, §11 item 19): `.` for the
    # root and a normalised root-escaping path for every clone, which is what
    # `gwz_family_model` records and refuses to spell any other way.
    return LocalFamilyMemberEntry(
        name=name,
        kind=kind,
        recorded_state=recorded,
        observed_state=observed,
        path=path if path is not None else f"../gwz-dev-{name}",
        last_error=last_error,
    )


class FamilyListingBridge(RecordingBridge):
    """Answers `local_family` with a populated `members` payload."""

    members: list[LocalFamilyMemberEntry] = []
    root_path: str | None = None

    async def call(
        self,
        method: str,
        request_message: str,
        response_message: str,
        request: Any,
    ) -> Any:
        self.calls.append((method, request_message, response_message, request))
        return LocalFamilyResponse(
            response=envelope(status=self.status, message=self.message),
            members=list(self.members),
            # LCM1.0c follow-up 3 (operator ruling 3 of 2026-09-06): the family
            # root's path, which a renderer joins with each member's
            # root-relative `path`.
            root_path=self.root_path,
        )


def listing_client_factory(
    members: list[LocalFamilyMemberEntry], root_path: str | None = DESIGN_ROOT
) -> Any:
    bridge = FamilyListingBridge(message="ok")
    bridge.members = members
    bridge.root_path = root_path

    def factory(**kwargs: Any) -> Client:
        return Client(bridge=bridge)

    return factory


#: The family of design §8.1, listed from D.
DESIGN_FAMILY = [
    member_entry("root", path="."),
    member_entry("A"),
    member_entry("B"),
    member_entry("C"),
    member_entry("D"),
    member_entry("hub", kind=LocalMemberKind.bare),
]


def test_local_list_renders_the_design_table(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Design §8.1: the absolute paths in the sample listing are literal, not
    # the driver's guess. Every `path` below is root-relative, exactly as the
    # wire carries it, and the column is the join with `root_path`.
    monkeypatch.setattr(cli, "Client", listing_client_factory(DESIGN_FAMILY))

    exit_code = cli.main(["local", "list"])

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "root  checkout  ready  /Users/limbo/gwz-dev\n"
        "A     checkout  ready  /Users/limbo/gwz-dev-A\n"
        "B     checkout  ready  /Users/limbo/gwz-dev-B\n"
        "C     checkout  ready  /Users/limbo/gwz-dev-C\n"
        "D     checkout  ready  /Users/limbo/gwz-dev-D\n"
        "hub   bare      ready  /Users/limbo/gwz-dev-hub\n"
    )


def listing_paths(
    members: list[LocalFamilyMemberEntry], root_path: str | None
) -> list[str]:
    """The path column `render_family_listing` produces, row by row."""

    rendered = cli_local_family.render_family_listing(
        LocalFamilyResponse(
            response=envelope(), members=members, root_path=root_path
        )
    )
    return [line.split()[-1] for line in rendered.splitlines()]


@pytest.mark.parametrize(
    "path,expected",
    [
        # Design §8.1: the root's own root-relative path is `.`.
        (".", "/Users/limbo/gwz-dev"),
        ("../gwz-dev-A", "/Users/limbo/gwz-dev-A"),
        ("../lanes/gwz-dev-E", "/Users/limbo/lanes/gwz-dev-E"),
        ("../../elsewhere/ws", "/Users/elsewhere/ws"),
    ],
)
def test_local_list_joins_the_member_path_against_the_observed_root(
    path: str, expected: str
) -> None:
    # `gwz_family_model` records a normalised, root-escaping member path, so
    # the join only has to resolve the leading `..` run -- lexically, without
    # touching a filesystem, because the response is all the renderer has.
    assert listing_paths([member_entry("A", path=path)], DESIGN_ROOT) == [expected]


@pytest.mark.parametrize("path", [".", "../gwz-dev-A"])
def test_local_list_without_a_root_path_renders_the_relative_path_unchanged(
    path: str,
) -> None:
    # `root_path` is absent exactly when `members` is (design §7), so a
    # populated listing without one is a core that has not caught up. Show the
    # path core did send rather than invent a root for it.
    assert listing_paths([member_entry("A", path=path)], None) == [path]


#: The cross-driver rendering fixture (LCM1.1 fix 3, lane C, 2026-09-06),
#: beside the argv fixture in gwz-core: every case is a `root_path`, a member
#: `path` and the display path BOTH drivers must render from them. The Rust
#: driver asserts the same file; a disagreement is a finding, never a reason
#: to bend the expected value.
LISTING_FIXTURE = PARITY_FIXTURE.with_name("local_family_listing_cases.json")

LISTING_FIXTURE_MISSING_REASON = (
    f"the cross-driver listing fixture is not in this checkout: {LISTING_FIXTURE}"
    " (it lives in the sibling gwz-core repository)"
)

#: The shapes the fixture must cover (the fix 3 brief), by case id.
REQUIRED_LISTING_CASES = {
    "plain-child",
    "sibling-through-parent",
    "nested-member",
    "two-level-escape",
    "absent-root",
    "empty-root",
    "already-absolute-member-path",
}


def listing_cases() -> list[Any]:
    if not LISTING_FIXTURE.exists():
        return [
            pytest.param(
                {},
                id="fixture-missing",
                marks=pytest.mark.skip(reason=LISTING_FIXTURE_MISSING_REASON),
            )
        ]
    document = json.loads(LISTING_FIXTURE.read_text(encoding="utf-8"))
    return [pytest.param(case, id=case["id"]) for case in document["cases"]]


@pytest.mark.parametrize("case", listing_cases())
def test_listing_fixture_case_renders_the_expected_display_path(
    case: dict[str, Any],
) -> None:
    # The join is lexical and never touches a filesystem, so the fixture's
    # paths need not exist. `expected` is spelled with `/`; a host whose
    # separator differs is compared after mapping it (fixture `_schema`).
    rendered = cli_local_family.member_display_path(case["root_path"], case["path"])
    assert rendered.replace(os.sep, "/") == case["expected"], case.get("note", "")


@pytest.mark.skipif(
    not LISTING_FIXTURE.exists(), reason=LISTING_FIXTURE_MISSING_REASON
)
def test_listing_fixture_covers_the_required_shapes_once_each() -> None:
    document = json.loads(LISTING_FIXTURE.read_text(encoding="utf-8"))
    ids = [case["id"] for case in document["cases"]]
    assert len(ids) == len(set(ids)), "case ids are unique"
    assert REQUIRED_LISTING_CASES <= set(ids)


def test_local_list_shows_a_divergent_observed_state_and_the_last_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Design §3.1: list is observation-only. An interrupted lane must be
    # visible as one, and a row whose observation contradicts what the index
    # recorded must show both -- an operator cannot act on a state that is
    # silently overwritten by the other.
    monkeypatch.setattr(
        cli,
        "Client",
        listing_client_factory(
            [
                member_entry("root", path="."),
                member_entry(
                    "B",
                    recorded=LocalMemberState.creating,
                    observed=LocalObservedState.incomplete,
                ),
                member_entry(
                    "D",
                    observed=LocalObservedState.missing,
                    last_error="destination was removed outside gwz",
                ),
            ]
        ),
    )

    exit_code = cli.main(["local", "list"])

    assert exit_code == 0
    assert capsys.readouterr().out == (
        "root  checkout  ready          /Users/limbo/gwz-dev\n"
        "B     checkout  incomplete     /Users/limbo/gwz-dev-B\n"
        "D     checkout  ready/missing  /Users/limbo/gwz-dev-D\n"
        "  last error: destination was removed outside gwz\n"
    )


@pytest.mark.parametrize(
    "recorded,observed,expected",
    [
        (LocalMemberState.ready, LocalObservedState.ready, "ready"),
        (LocalMemberState.creating, LocalObservedState.incomplete, "incomplete"),
        (
            LocalMemberState.disposing,
            LocalObservedState.interrupted_disposal,
            "interrupted_disposal",
        ),
        (LocalMemberState.ready, LocalObservedState.missing, "ready/missing"),
        (
            LocalMemberState.disposing,
            LocalObservedState.pointer_removed,
            "disposing/pointer_removed",
        ),
        (
            LocalMemberState.ready,
            LocalObservedState.mismatched,
            "ready/mismatched",
        ),
        (
            LocalMemberState.creating,
            LocalObservedState.malformed,
            "creating/malformed",
        ),
        (
            LocalMemberState.ready,
            LocalObservedState.unobserved,
            "ready/unobserved",
        ),
    ],
)
def test_local_list_state_cell_covers_every_observed_state(
    recorded: LocalMemberState,
    observed: LocalObservedState,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "Client",
        listing_client_factory(
            [member_entry("A", recorded=recorded, observed=observed)]
        ),
    )

    exit_code = cli.main(["local", "list"])

    assert exit_code == 0
    line = capsys.readouterr().out.rstrip("\n")
    assert line.split() == ["A", "checkout", expected, "/Users/limbo/gwz-dev-A"]


def test_local_list_json_carries_every_member_field_and_the_root_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The machine form carries both wire fields faithfully: `root_path` as core
    # sent it and each member's `path` still root-relative. The join is a
    # presentation of the human table, never a rewrite of the payload -- a
    # reader that wants absolute paths has both halves and can join them too.
    monkeypatch.setattr(
        cli,
        "Client",
        listing_client_factory(
            [
                member_entry("root", path="."),
                member_entry(
                    "hub",
                    kind=LocalMemberKind.bare,
                    recorded=LocalMemberState.disposing,
                    observed=LocalObservedState.interrupted_disposal,
                    last_error="disposal was interrupted after the pointer",
                ),
            ]
        ),
    )

    exit_code = cli.main(["--json", "local", "list"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["root_path"] == "/Users/limbo/gwz-dev"
    assert payload["members"] == [
        {
            "name": "root",
            "kind": "checkout",
            "recorded_state": "ready",
            "observed_state": "ready",
            "path": ".",
            "last_error": None,
        },
        {
            "name": "hub",
            "kind": "bare",
            "recorded_state": "disposing",
            "observed_state": "interrupted_disposal",
            "path": "../gwz-dev-hub",
            "last_error": "disposal was interrupted after the pointer",
        },
    ]
    assert payload["response"]["meta"]["action"] == "local_family"


@pytest.mark.parametrize("machine_flag", ["--json", "--jsonl"])
def test_local_list_machine_output_spells_every_enum_in_snake_case(
    machine_flag: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Operator ruling 2 of 2026-09-06 (design §11 item 18).

    Machine output spells every enum value in the protocol's own snake_case,
    in both drivers. This driver already did; the assertion is against the
    generated enum's `name`, not a word spelled out here, so a renderer that
    invented its own vocabulary -- PascalCase or otherwise -- fails even if it
    happened to agree on these three values.
    """

    entry = member_entry(
        "hub",
        kind=LocalMemberKind.bare,
        recorded=LocalMemberState.disposing,
        observed=LocalObservedState.interrupted_disposal,
    )
    monkeypatch.setattr(cli, "Client", listing_client_factory([entry]))

    exit_code = cli.main([machine_flag, "local", "list"])

    assert exit_code == 0
    member = json.loads(capsys.readouterr().out)["members"][0]
    assert member["kind"] == LocalMemberKind.bare.name == "bare"
    assert member["recorded_state"] == LocalMemberState.disposing.name == "disposing"
    assert (
        member["observed_state"]
        == LocalObservedState.interrupted_disposal.name
        == "interrupted_disposal"
    )


def test_local_list_json_carries_an_absent_root_path_as_null(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli, "Client", listing_client_factory([member_entry("A")], root_path=None)
    )

    exit_code = cli.main(["--json", "local", "list"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["root_path"] is None
    assert [member["path"] for member in payload["members"]] == ["../gwz-dev-A"]


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
