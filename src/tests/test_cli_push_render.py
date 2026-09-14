"""Human rendering of push `noop` reasons, which core carries in `planned.message`
(GwzUrlSchemePushPlan §3.6, D11). The responses are synthetic until core
classifies repositories (step 3.5)."""
from __future__ import annotations

from gwz.cli_render import render_response
from gwz.cli_render_parts.push import is_unchecked_reason
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    MaterializeResponse,
    MemberResponse,
    MemberStatus,
    PlannedAction,
    PlannedChange,
    PushResponse,
    ResponseEnvelope,
    ResponseMeta,
    SourceKind,
    TargetKind,
    TransportCredentialMethod,
    TransportObservation,
    TransportOperation,
    TransportSelectionSource,
)

CHECKED = "already on origin"
UP_TO_DATE = "up to date with origin/main as of the last fetch or push"
BEHIND = "behind origin/main as of the last fetch or push"


def _row(path: str, action: PlannedAction | None, message: str | None = None) -> MemberResponse:
    return MemberResponse(
        member_id=f"mem_{path.rsplit('/', 1)[-1]}",
        member_path=path,
        source_kind=SourceKind.git,
        status=MemberStatus.noop if action is PlannedAction.noop else MemberStatus.ok,
        error=None,
        planned=(
            None
            if action is None
            else PlannedChange(action=action, from_ref=None, to_ref=None, message=message)
        ),
        state=None,
        git_status=None,
        lock_match=None,
        target_kind=TargetKind.member,
        lock_difference_reasons=None,
        url_resolution=None,
    )


def _envelope(
    action: ActionKind, aggregate: AggregateStatus, rows: tuple[MemberResponse, ...]
) -> ResponseEnvelope:
    return ResponseEnvelope(
        meta=ResponseMeta(
            request_id="req_push",
            schema_version="gwz.protocol/v0",
            action=action,
            aggregate_status=aggregate,
            operation_id="op_push",
            message=None,
            attribution=None,
            transport=None,
        ),
        members=list(rows),
        errors=[],
    )


def _push(*rows: MemberResponse, aggregate: AggregateStatus = AggregateStatus.noop) -> PushResponse:
    return PushResponse(response=_envelope(ActionKind.push, aggregate, rows))


def test_only_reasons_from_the_last_fetch_or_push_count_as_unchecked() -> None:
    assert is_unchecked_reason(UP_TO_DATE)
    assert is_unchecked_reason("behind origin/release/1.0 as of the last fetch or push")
    assert not is_unchecked_reason(CHECKED)
    assert not is_unchecked_reason(None)


def test_summary_counts_the_repositories_that_were_not_checked() -> None:
    response = _push(
        _row("repos/app", PlannedAction.noop, UP_TO_DATE),
        _row("repos/lib", PlannedAction.noop, BEHIND),
        _row("repos/cli", PlannedAction.noop, CHECKED),
        _row("repos/doc", PlannedAction.push),
        aggregate=AggregateStatus.ok,
    )

    assert render_response(response) == (
        "ok\n2 repositories unchanged since the last fetch or push were not checked "
        "for changes; --check-remotes to verify"
    )
    assert "--check-remotes" not in render_response(response, json_mode=True)


def test_summary_names_one_repository_in_the_singular() -> None:
    response = _push(
        _row("repos/app", PlannedAction.noop, UP_TO_DATE),
        _row("repos/lib", PlannedAction.noop, CHECKED),
    )

    assert render_response(response) == (
        "noop\n1 repository unchanged since the last fetch or push was not checked "
        "for changes; --check-remotes to verify"
    )


def test_no_summary_without_an_unchecked_push_noop_row() -> None:
    assert render_response(_push(_row("repos/app", PlannedAction.noop, CHECKED))) == "noop"
    pushed = _push(_row("repos/app", PlannedAction.push, UP_TO_DATE), aggregate=AggregateStatus.ok)
    assert render_response(pushed) == "ok"
    # Other operations also plan `noop` rows; only push reasons are rendered.
    materialized = MaterializeResponse(
        response=_envelope(
            ActionKind.materialize,
            AggregateStatus.ok,
            (_row("repos/app", PlannedAction.noop, UP_TO_DATE),),
        )
    )
    assert render_response(materialized, show_transport=True) == "ok"


def test_verbose_lists_both_kinds_of_reason_before_the_transport_diagnostics() -> None:
    response = _push(
        _row("repos/app", PlannedAction.noop, UP_TO_DATE),
        _row("repos/lib", PlannedAction.noop, CHECKED),
        _row("repos/doc", PlannedAction.push),
        aggregate=AggregateStatus.ok,
    )
    response.response.meta.transport = [
        TransportObservation(
            repository_path="repos/doc",
            remote="origin",
            operation=TransportOperation.push,
            credential_method=TransportCredentialMethod.agent,
            selection_source=TransportSelectionSource.ambient,
            credential_offered=True,
            authenticated=True,
            public_key_fingerprint=None,
        )
    ]

    lines = render_response(response, show_transport=True).splitlines()

    assert lines[2:4] == [f"repos/app: {UP_TO_DATE}", f"repos/lib: {CHECKED}"]
    assert lines[4].startswith("repos/doc origin: credential=agent")
    assert len(lines) == 5
    assert CHECKED not in render_response(response)
