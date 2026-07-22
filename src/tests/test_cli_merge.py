import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
import pytest
from gwz import cli
from gwz.cli import build_parser
from gwz.cli_render import operation_event_json, render_response
from gwz.cli_shared import CliUsageError, CommandContext, meta_kwargs, validate_args
from gwz.errors import GwzBridgeError, GwzOperationError
from gwz.protocol.generated import (
    ActionKind, AggregateStatus, EventKind, MergeAnalysisKind, MergeOperationState, MergeOp,
    MergeOperationDrift, MergeOperationDriftKind, MergeParticipantCounts,
    MergeParticipantDrift, MergeParticipantDriftKind, MergeParticipantState,
    MergePendingActionKind, MergePendingActionState, MergePendingActionSummary,
    MergePreservation, MergePublicationStep, MergeRepoSummary, MergeResponse,
    GwzError, GwzErrorCode, OperationEvent, OperationResult, ResponseEnvelope, ResponseMeta, Severity,
    TargetKind,
)
from native_helpers import commit_file, git, native_client

class FakeMergeHandle:
    def __init__(self, response: Any, events: list[OperationEvent] | None = None) -> None:
        self.operation_id = "op-fake"
        self.response = response
        self.stream_events = events or []
    def events(self):
        async def iterate():
            for event in self.stream_events:
                yield event
        return iterate()
    async def result(self) -> Any:
        return self.response

class FakeClient:
    def __init__(
        self,
        *args: Any,
        response: Any = "merge",
        events: list[OperationEvent] | None = None,
        **kwargs: Any,
    ) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.response = response
        self.events = events or []
    async def __aenter__(self) -> "FakeClient":
        return self
    async def __aexit__(self, *args: Any) -> None:
        return None
    async def merge(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        return self.response
    async def merge_stream(self, *args: Any, **kwargs: Any) -> FakeMergeHandle:
        self.calls.append((args, kwargs))
        return FakeMergeHandle(self.response, self.events)

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
    run_handler(["merge", "--status"], client)
    assert client.calls[2] == ((None,), {
        "op": MergeOp.status, "merge_id": None, "mode": None, "message": None,
        "preserve": None,
    })
    with pytest.raises(CliUsageError, match="one lifecycle"):
        run_handler(["merge", "--continue", "--abort"], client)
    with pytest.raises(CliUsageError, match="mutually exclusive"):
        run_handler(["merge", "feature/x", "--ff-only", "--no-ff"], client)

def test_merge_help_hides_lifecycle_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["merge", "--help"])
    help_text = capsys.readouterr().out
    assert "--continue" not in help_text
    assert "--abort" not in help_text
    assert "--status" not in help_text

def test_merge_human_rendering_reports_open_status_and_structured_drift() -> None:
    human = render_response(merge_response())
    assert human == canonical_merge_status_human_fixture().read_text().rstrip()
    assert "gwz merge --continue" not in human
    assert "gwz merge --abort" not in human


def test_merge_human_and_machine_render_idle_without_fabricated_operation() -> None:
    response = MergeResponse(
        ResponseEnvelope(ResponseMeta(
            "req-idle", "gwz.protocol/v0", ActionKind.merge, AggregateStatus.noop,
            "op-idle", None, None,
        ), [], []),
        None,
        MergeOperationState.idle,
        False,
        MergeParticipantCounts(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        [], [], None, None,
    )

    assert render_response(response) == (
        "action: merge\nstatus: Noop\nstate: idle\nNo coordinated merge is open."
    )
    machine = json.loads(render_response(response, json_mode=True))["merge"]
    assert machine["merge_id"] is None
    assert machine["state"] == "Idle"
    assert machine["open"] is False
    assert machine["participant_counts"]["total"] == 0
    assert machine["repos"] == []
    assert machine["operation_drift"] == []

def test_merge_json_success_matches_rust_parity_fixture(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    response = merge_response()
    monkeypatch.setattr(cli, "Client", lambda **kwargs: FakeClient(response=response))
    cli.main(["--json", "merge", "feature/x"])
    expected = json.loads(canonical_merge_response_fixture().read_text())
    assert json.loads(capsys.readouterr().out) == expected


def test_merge_jsonl_streams_events_then_one_final_response(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    response = merge_response()
    event = merge_event()
    monkeypatch.setattr(
        cli,
        "Client",
        lambda **kwargs: FakeClient(response=response, events=[event]),
    )

    assert cli.main(["--jsonl", "merge", "feature/x"]) == 1

    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records == [
        operation_event_json(event),
        json.loads(canonical_merge_response_fixture().read_text()),
    ]


def test_merge_jsonl_flushes_event_before_operation_completes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    event = merge_event()
    response = merge_response()

    class PausedHandle(FakeMergeHandle):
        def __init__(self) -> None:
            super().__init__(response)
            self.waiting = asyncio.Event()
            self.release = asyncio.Event()

        def events(self):
            async def iterate():
                yield event
                self.waiting.set()
                await self.release.wait()

            return iterate()

    handle = PausedHandle()

    class PausedClient(FakeClient):
        async def merge_stream(self, *args: Any, **kwargs: Any) -> FakeMergeHandle:
            return handle

    monkeypatch.setattr(cli, "Client", PausedClient)

    async def exercise() -> None:
        args = build_parser().parse_args(["--jsonl", "merge", "feature/x"])
        task = asyncio.create_task(cli.run(args))
        await asyncio.wait_for(handle.waiting.wait(), timeout=1)

        early_records = [
            json.loads(line) for line in capsys.readouterr().out.splitlines()
        ]
        assert early_records == [operation_event_json(event)]
        assert not task.done()

        handle.release.set()
        assert await task == 1

    asyncio.run(exercise())

    final_records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert final_records == [json.loads(canonical_merge_response_fixture().read_text())]


def test_native_merge_jsonl_subprocess_flushes_before_completion(tmp_path: Path) -> None:
    client = native_client(tmp_path)
    asyncio.run(client.create_workspace(workspace_id="ws_jsonl_live"))
    environment = dict(os.environ)
    environment["GWZ_PY_TEST_EVENT_DELAY_MS"] = "750"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gwz.cli",
            "--root",
            str(tmp_path),
            "--jsonl",
            "merge",
            "--status",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    try:
        assert process.stdout is not None
        first_line = process.stdout.readline()
        first = json.loads(first_line)
        assert first["kind"] == "event"
        assert first["event_kind"] == "OperationStarted"
        assert process.poll() is None

        remaining, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert process.returncode == 0, stderr
    records = [first, *(json.loads(line) for line in remaining.splitlines())]
    assert all(record["kind"] == "event" for record in records[:-1])
    assert records[-1]["kind"] == "response"
    assert records[-1]["merge"]["state"] == "Idle"


def test_merge_event_json_matches_rust_canonical_fixture() -> None:
    expected = json.loads(canonical_merge_event_fixture().read_text())
    assert operation_event_json(merge_event()) == expected


def merge_event() -> OperationEvent:
    member = MergeRepoSummary(
        target_id="mem_app",
        target_kind=TargetKind.member,
        path="repos/app",
        source_ref="feature/x",
        source_commit="source123",
        target_branch="main",
        before_commit="before123",
        resulting_commit="merge123",
        live_commit="merge123",
        state=MergeParticipantState.merged,
        predicted=None,
        prediction_complete=None,
        conflict_paths=[],
        continue_eligible=None,
        abort_eligible=None,
        drift=[],
        error=None,
        pending_action=None,
    )
    return OperationEvent(
        operation_id="op_render",
        request_id="req_render",
        sequence=0,
        timestamp_ms=1,
        kind=EventKind.member_finished,
        severity=Severity.info,
        member_id="mem_app",
        member_path="repos/app",
        message=None,
        member=None,
        error=None,
        attribution=None,
        progress=None,
        target_kind=TargetKind.member,
        merge_state=MergeOperationState.finalizing,
        merge_member=member,
        artifact_path=".gwz/merge/merge_1.yaml",
    )

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
        async def merge_stream(self, *args: Any, **kwargs: Any) -> FakeMergeHandle:
            raise GwzBridgeError("call failed: MergePhaseUnsupported: reserved")
    monkeypatch.setattr(cli, "Client", FailingClient)
    assert cli.main([flag, "merge", "feature/x", "--ff-only"]) == 1
    error = json.loads(capsys.readouterr().out.splitlines()[-1])["errors"][0]
    assert (error["code"], error["message"]) == ("MergePhaseUnsupported", "reserved")


@pytest.mark.parametrize("flag", ["--json", "--jsonl"])
def test_native_preflight_machine_error_retains_second_member_context(
    flag: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = native_client(tmp_path)
    asyncio.run(client.create_workspace(workspace_id="ws_merge_error"))
    asyncio.run(client.create_repo("app", member_id="mem_app", source_id="src_app"))
    asyncio.run(client.create_repo("lib", member_id="mem_lib", source_id="src_lib"))
    app = tmp_path / "app"
    lib = tmp_path / "lib"
    commit_file(app, "README.md", "app\n", "initial")
    commit_file(lib, "README.md", "lib\n", "initial")
    asyncio.run(client.capture(paths=["app", "lib"]))
    git(app, "checkout", "-b", "feature/source")
    commit_file(app, "source.txt", "source\n", "source")
    git(app, "checkout", "main")

    assert cli.main(["--root", str(tmp_path), flag, "merge", "feature/source"]) == 1

    error = json.loads(capsys.readouterr().out.splitlines()[-1])["errors"][0]
    assert error["code"] == "GitCommandFailed"
    assert error["member_id"] == "mem_lib"
    assert error["member_path"] == "lib"
    assert error["target_kind"] == "Member"


def test_merge_jsonl_failure_ends_with_structured_terminal_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    event = merge_event()
    member_error = GwzError(
        GwzErrorCode.git_command_failed,
        "member source is missing",
        "mem_lib",
        "lib",
        "source ref was not found",
        TargetKind.member,
    )
    terminal = OperationResult(
        "op-fake", "req-fake", ActionKind.merge, AggregateStatus.failed,
        1, 2, [], [member_error], None,
    )

    class FailingHandle(FakeMergeHandle):
        async def result(self) -> Any:
            raise GwzOperationError(
                "gwz operation returned failed",
                response=terminal,
                aggregate_status=terminal.aggregate_status,
                operation_id=terminal.operation_id,
                request_id=terminal.request_id,
                member_errors=terminal.errors,
            )

    class FailingStreamClient(FakeClient):
        async def merge_stream(self, *args: Any, **kwargs: Any) -> FakeMergeHandle:
            return FailingHandle(None, [event])

    monkeypatch.setattr(cli, "Client", FailingStreamClient)

    assert cli.main(["--jsonl", "merge", "feature/x"]) == 1

    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert records[0] == operation_event_json(event)
    assert records[-1]["kind"] == "response"
    assert records[-1]["errors"] == [{
        "code": "GitCommandFailed",
        "message": "member source is missing",
        "member_id": "mem_lib",
        "member_path": "lib",
        "detail": "source ref was not found",
        "target_kind": "Member",
    }]
    assert len(records) == 2

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
        "req-parity-1", "gwz.protocol/v0", ActionKind.merge, AggregateStatus.failed,
        "op-parity-1", None, None,
    ), [], [])
    repos = [
        merge_repo("lib", MergeParticipantState.planned),
        merge_repo("docs", MergeParticipantState.conflicted),
        merge_repo("api", MergeParticipantState.continued),
        merge_repo("tools", MergeParticipantState.aborted),
        merge_repo("web", MergeParticipantState.rolled_back),
        merge_repo("worker", MergeParticipantState.failed),
    ]
    repos[0].predicted = MergeAnalysisKind.true_merge
    repos[0].pending_action = MergePendingActionSummary(
        MergePendingActionKind.true_merge,
        MergePendingActionState.not_started,
        "Git action is durably journaled and has not started",
    )
    repos[1].conflict_paths = ["guide.md"]
    repos[1].prediction_complete = False
    repos[1].continue_eligible = False
    repos[1].abort_eligible = True
    repos[1].live_commit = "before123"
    repos[1].drift = [MergeParticipantDrift(
        MergeParticipantDriftKind.head_advanced,
        "HEAD advanced while merge was open",
        "main", "main", "before123", "live456", "source123", "source123",
    )]
    for index, commit in [(2, "continued123"), (3, "before123"), (4, "before123")]:
        repos[index].prediction_complete = True
        repos[index].continue_eligible = False
        repos[index].abort_eligible = False
        repos[index].resulting_commit = commit
        repos[index].live_commit = commit
    member_error = GwzError(
        GwzErrorCode.git_command_failed,
        "member 'mem_worker' at 'worker': revspec 'feature/x' not found",
        "mem_worker",
        "worker",
        "source ref was not found in the member repository",
        TargetKind.member,
    )
    repos[5].prediction_complete = False
    repos[5].continue_eligible = False
    repos[5].abort_eligible = False
    repos[5].error = member_error
    return MergeResponse(
        ResponseEnvelope(envelope.meta, envelope.members, [member_error]),
        "merge-parity-1",
        MergeOperationState.halted,
        True,
        MergeParticipantCounts(6, 1, 0, 0, 0, 1, 1, 0, 1, 1, 1),
        repos,
        [MergeOperationDrift(
            MergeOperationDriftKind.baseline_manifest_changed,
            "manifest changed after planning",
        )],
        [MergePreservation(
            "mem_docs", "docs", "refs/gwz/preserve/merge-parity-1/mem_docs",
            "backup123", "stash-parity-1", "stashobj123",
        )],
        MergePublicationStep.verifying_publication,
    )

def merge_repo(path: str, state: MergeParticipantState) -> MergeRepoSummary:
    return MergeRepoSummary(f"mem_{path}", TargetKind.member, path, "feature/x", "source123",
                            "main", "before123", None, None, state, None, None, [],
                            None, None, [], None, None)


def canonical_merge_response_fixture() -> Path:
    # Driver development already requires the sibling gwz-core checkout. Keeping the
    # canonical fixture there lets both suites enforce one contract without copies.
    return (
        Path(__file__).resolve().parents[3]
        / "gwz-core/protocol/fixtures/cli_parity/merge_response.json"
    )


def canonical_merge_event_fixture() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "gwz-core/protocol/fixtures/cli_parity/merge_event.json"
    )


def canonical_merge_status_human_fixture() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "gwz-cli/tests/fixtures/merge_status_human.txt"
    )
