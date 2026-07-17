import asyncio
import json
from pathlib import Path
from typing import Any
import pytest
from gwz import cli
from gwz.cli import build_parser
from gwz.cli_render import render_response
from gwz.cli_shared import CliUsageError, CommandContext, meta_kwargs, validate_args
from gwz.errors import GwzBridgeError, GwzOperationError
from gwz.protocol.generated import (
    ActionKind, AggregateStatus, MergeAnalysisKind, MergeOperationState, MergeOp,
    MergeParticipantCounts, MergeParticipantState, MergeRepoSummary, MergeResponse,
    ResponseEnvelope, ResponseMeta, TargetKind,
)

class FakeClient:
    def __init__(self, *args: Any, response: Any = "merge", **kwargs: Any) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.response = response
    async def __aenter__(self) -> "FakeClient":
        return self
    async def __aexit__(self, *args: Any) -> None:
        return None
    async def merge(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        return self.response

def run_handler(argv: list[str], client: FakeClient) -> Any:
    args = build_parser().parse_args(argv)
    validate_args(args)
    return asyncio.run(args.command_handler(CommandContext(args, client, meta_kwargs(args))))

def test_merge_routes_start_reserved_fields_and_rejects_ambiguity() -> None:
    client = FakeClient()
    run_handler(["merge", "feature/x", "--dry-run"], client)
    assert client.calls[0] == (("feature/x",), {
        "op": MergeOp.start, "merge_id": None, "mode": None, "message": None,
        "preserve": None, "dry_run": True,
    })
    run_handler(["merge", "feature/x", "--continue", "--preserve", "-m", "custom"], client)
    reserved = client.calls[1][1]
    assert (reserved["op"], reserved["preserve"], reserved["message"]) == (
        MergeOp.resume, True, "custom")
    with pytest.raises(CliUsageError, match="one lifecycle"):
        run_handler(["merge", "--continue", "--abort"], client)
    with pytest.raises(CliUsageError, match="mutually exclusive"):
        run_handler(["merge", "feature/x", "--ff-only", "--no-ff"], client)

def test_merge_help_hides_lifecycle_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["merge", "--help"])
    help_text = capsys.readouterr().out
    assert "--continue" not in help_text and "--abort" not in help_text

def test_merge_human_rendering_matches_m0_contract() -> None:
    human = render_response(merge_response())
    assert "action: merge" in human
    assert "lib  feature/x -> main  planned (merge commit)" in human
    assert "docs  feature/x -> main  conflicted  guide.md" in human
    assert "ordinary Git commands in docs/." in human
    assert "gwz merge --continue" not in human

@pytest.mark.parametrize("flag", ["--json", "--jsonl"])
def test_merge_machine_success_matches_rust_parity_fixture(
    flag: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    response = merge_response()
    monkeypatch.setattr(cli, "Client", lambda **kwargs: FakeClient(response=response))
    cli.main([flag, "merge", "feature/x"])
    expected = json.loads((Path(__file__).parent / "fixtures/cli_parity/merge_response.json").read_text())
    assert json.loads(capsys.readouterr().out) == expected

@pytest.mark.parametrize("flag", ["--json", "--jsonl"])
@pytest.mark.parametrize("options", [["--continue", "--abort"], ["--ff-only", "--no-ff"]])
def test_merge_semantic_errors_are_typed_invalid_request(
    flag: str, options: list[str], monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "Client", FakeClient)
    assert cli.main([flag, "merge", *options]) == 2
    error = json.loads(capsys.readouterr().out)["errors"][0]
    assert error["code"] == "InvalidRequest"

@pytest.mark.parametrize("flag", ["--json", "--jsonl"])
def test_native_machine_errors_are_structured(
    flag: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class FailingClient(FakeClient):
        async def merge(self, *args: Any, **kwargs: Any) -> None:
            raise GwzBridgeError("call failed: MergePhaseUnsupported: reserved")
    monkeypatch.setattr(cli, "Client", FailingClient)
    assert cli.main([flag, "merge", "feature/x", "--ff-only"]) == 1
    error = json.loads(capsys.readouterr().out)["errors"][0]
    assert (error["code"], error["message"]) == ("MergePhaseUnsupported", "reserved")

def test_halted_merge_response_unwraps_without_changing_generic_failures() -> None:
    response = merge_response()
    response.state = MergeOperationState.halted
    response.response.meta.aggregate_status = AggregateStatus.failed
    error = GwzOperationError("halted", response=response)
    args = build_parser().parse_args(["merge", "feature/x"])
    assert cli._renderable_operation_response(args, error) is response
    assert cli._exit_code_for_cli_response(args, response) == 1
    rendered = json.loads(render_response(response, json_mode=True))
    assert (rendered["meta"]["aggregate_status"], rendered["merge"]["state"]) == (
        "Failed", "Halted")
    assert cli._renderable_operation_response(
        args, GwzOperationError("other", response=object())
    ) is None

def merge_response() -> MergeResponse:
    envelope = ResponseEnvelope(ResponseMeta(
        "req", "gwz.protocol/v0", ActionKind.merge, AggregateStatus.conflicted,
        "op", None, None,
    ), [], [])
    repos = [merge_repo("lib", MergeParticipantState.planned),
             merge_repo("docs", MergeParticipantState.conflicted)]
    repos[0].predicted = MergeAnalysisKind.true_merge
    repos[1].conflict_paths = ["guide.md"]
    return MergeResponse(envelope, None, MergeOperationState.awaiting_resolution, False,
                         MergeParticipantCounts(2, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0),
                         repos, [], None, None)

def merge_repo(path: str, state: MergeParticipantState) -> MergeRepoSummary:
    return MergeRepoSummary(f"mem_{path}", TargetKind.member, path, "feature/x", "source",
                            "main", "before", None, None, state, None, None, [],
                            None, None, [], None)
