from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from gwz import cli
from gwz.cli import build_parser
from gwz.cli_render import render_response
from gwz.cli_shared import CliUsageError, CommandContext, meta_kwargs, validate_args
from gwz.errors import GwzBridgeError
from gwz.protocol.generated import (
    ActionKind,
    AggregateStatus,
    MergeAnalysisKind,
    MergeOperationState,
    MergeOp,
    MergeParticipantCounts,
    MergeParticipantState,
    MergeRepoSummary,
    MergeResponse,
    ResponseEnvelope,
    ResponseMeta,
    TargetKind,
)


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def merge(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append((args, kwargs))
        return "merge"


def run_handler(argv: list[str], client: FakeClient) -> Any:
    args = build_parser().parse_args(argv)
    validate_args(args)
    context = CommandContext(args=args, client=client, meta=meta_kwargs(args))
    return asyncio.run(args.command_handler(context))


def test_merge_start_and_reserved_combinations_reach_client() -> None:
    client = FakeClient()
    run_handler(["merge", "feature/x", "--dry-run"], client)
    assert client.calls[0] == (
        ("feature/x",),
        {
            "op": MergeOp.start,
            "merge_id": None,
            "mode": None,
            "message": None,
            "preserve": None,
            "dry_run": True,
        },
    )

    run_handler(["merge", "feature/x", "--continue", "--preserve", "-m", "custom"], client)
    assert client.calls[1][1]["op"] is MergeOp.resume
    assert client.calls[1][1]["preserve"] is True
    assert client.calls[1][1]["message"] == "custom"

    with pytest.raises(CliUsageError, match="one lifecycle"):
        run_handler(["merge", "--continue", "--abort"], client)
    with pytest.raises(CliUsageError, match="mutually exclusive"):
        run_handler(["merge", "feature/x", "--ff-only", "--no-ff"], client)


def test_merge_help_does_not_advertise_lifecycle_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        build_parser().parse_args(["merge", "--help"])
    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "--continue" not in help_text
    assert "--abort" not in help_text


def test_merge_human_and_machine_rendering_matches_m0_contract() -> None:
    response = merge_response()
    human = render_response(response)
    assert "action: merge" in human
    assert "lib  feature/x -> main  planned (merge commit)" in human
    assert "docs  feature/x -> main  conflicted  guide.md" in human
    assert "ordinary Git commands in docs/." in human
    assert "gwz merge --continue" not in human

    machine = json.loads(render_response(response, json_mode=True))
    assert machine["response"]["meta"]["action"] == "merge"
    assert machine["repos"][1]["state"] == "conflicted"


def merge_response() -> MergeResponse:
    envelope = ResponseEnvelope(
        meta=ResponseMeta(
            "req",
            "gwz.protocol/v0",
            ActionKind.merge,
            AggregateStatus.conflicted,
            "op",
            None,
            None,
        ),
        members=[],
        errors=[],
    )
    repos = [
        merge_repo("lib", MergeParticipantState.planned),
        merge_repo("docs", MergeParticipantState.conflicted),
    ]
    repos[0].predicted = MergeAnalysisKind.true_merge
    repos[1].conflict_paths = ["guide.md"]
    return MergeResponse(
        envelope,
        None,
        MergeOperationState.awaiting_resolution,
        False,
        MergeParticipantCounts(2, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0),
        repos,
        [],
        None,
        None,
    )


def merge_repo(path: str, state: MergeParticipantState) -> MergeRepoSummary:
    return MergeRepoSummary(
        f"mem_{path}",
        TargetKind.member,
        path,
        "feature/x",
        "source",
        "main",
        "before",
        "result" if state is MergeParticipantState.merged else None,
        None,
        state,
        None,
        None,
        [],
        None,
        None,
        [],
        None,
    )


@pytest.mark.parametrize("flag", ["--json", "--jsonl"])
def test_machine_errors_are_structured(
    flag: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class FailingClient:
        def __init__(self, root: str | None = None) -> None:
            self.root = root

        async def __aenter__(self) -> "FailingClient":
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def merge(self, *args: Any, **kwargs: Any) -> None:
            raise GwzBridgeError(
                "native bridge call failed for merge: MergePhaseUnsupported: reserved"
            )

    monkeypatch.setattr(cli, "Client", FailingClient)
    assert cli.main([flag, "merge", "feature/x", "--ff-only"]) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["errors"][0] == {
        "code": "MergePhaseUnsupported",
        "message": "reserved",
    }
