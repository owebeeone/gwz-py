"""End-to-end push through gwz-py on the native core, and its parity with the Rust
CLI (push plan steps 3.5 and 3.8, `dev-docs/GwzUrlSchemePushPlan.md` §3.5 and
§3.6).

By default a push classifies each repository against its last-known ref and
contacts only what changed; `--check-remotes` (D7) reads every destination. A
`noop` row carries its reason in `planned.message` (D11), and human output adds a
line counting the repositories that were not checked. The two CLIs share that
text and the JSON rows, not the layout.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from gwz.protocol.generated import (
    AggregateStatus,
    MemberStatus,
    PlannedAction,
    RemoteCheck,
    TransportOperation,
)

from native_helpers import (
    bare_ref,
    commit_file,
    create_git_repo,
    git,
    init_bare_repo,
    native_client,
    native_module,
)

UP_TO_DATE = "up to date with origin/main as of the last fetch or push"
BEHIND = "behind origin/main as of the last fetch or push"
ON_ORIGIN = "already on origin"
ONE_UNCHECKED = (
    "1 repository unchanged since the last fetch or push was not checked for changes; "
    "--check-remotes to verify"
)
TWO_UNCHECKED = (
    "2 repositories unchanged since the last fetch or push were not checked for changes; "
    "--check-remotes to verify"
)
THREE_UNCHECKED = (
    "3 repositories unchanged since the last fetch or push were not checked for changes; "
    "--check-remotes to verify"
)
CHECK_REMOTES_HELP = (
    "Read every selected remote and every root dependency instead of skipping "
    "repositories that are unchanged since the last fetch or push"
)
REFSPEC = "refs/heads/main:refs/heads/main"
PYTHON_CLI = [sys.executable, "-m", "gwz.cli"]
# The push targets, as (member id, path), in the order both CLIs report them.
APP = ("mem_app", "app")
LIB = ("mem_lib", "lib")
ROOT = ("@root", ".")
TARGETS = (APP, LIB, ROOT)


def run_cli(driver: list[str], workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a CLI on `workspace` from its root, as an operator would."""
    return subprocess.run(
        [*driver, "--root", str(workspace), *args],
        cwd=workspace,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


def run_ok(driver: list[str], workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = run_cli(driver, workspace, *args)
    assert result.returncode == 0, (args, result.stdout, result.stderr)
    return result


class PublishedWorkspace:
    """A workspace with members `app` and `lib` and a root, each with a local bare
    remote, after one push published the root. Every repository then has a
    last-known ref (§3.5): the members from `init`'s clones, the root from that
    push. `app` and `lib` hold two commits each."""

    def __init__(self, tmp_path: Path) -> None:
        native_module()
        self.remotes = tmp_path / "remotes"
        for name in ("app", "lib", "root"):
            init_bare_repo(self.remote(name))
            git(self.remote(name), "symbolic-ref", "HEAD", "refs/heads/main")
        for name in ("app", "lib"):
            seed = tmp_path / "seeds" / name
            create_git_repo(seed)
            commit_file(seed, "README.md", f"{name} two\n", "second")
            git(seed, "push", str(self.remote(name)), "main:refs/heads/main")
        self.root = tmp_path / "workspace"
        self.root.mkdir()
        run_ok(PYTHON_CLI, self.root, "init", str(self.remote("app")), str(self.remote("lib")))
        for _, path in TARGETS:
            git(self.root / path, "config", "user.name", "GWZ Test")
            git(self.root / path, "config", "user.email", "gwz@example.invalid")
        git(self.root, "remote", "add", "origin", str(self.remote("root")))
        git(self.root, "add", "-A")
        git(self.root, "commit", "-m", "workspace")
        run_ok(PYTHON_CLI, self.root, "push")
        assert self.remote_heads() == self.heads()

    def remote(self, name: str) -> Path:
        return self.remotes / f"{name}.git"

    def head(self, path: str) -> str:
        return git(self.root / path, "rev-parse", "HEAD")

    def heads(self) -> list[str | None]:
        """The head of `app`, `lib` and the root, in that order."""
        return [self.head(path) for _, path in TARGETS]

    def remote_heads(self) -> list[str | None]:
        """What the bare remotes of `app`, `lib` and the root hold on `main`."""
        return [bare_ref(self.remote(name), "refs/heads/main") for name in ("app", "lib", "root")]

    def commit_a_change_in_app(self) -> None:
        """Commit a new file in `app` through gwz-py, which records the commit in
        the root lock and commits the root."""
        (self.root / APP[1] / "CHANGE.md").write_text("change\n", encoding="utf-8")
        run_ok(PYTHON_CLI, self.root, "add", f"{APP[1]}/CHANGE.md")
        run_ok(PYTHON_CLI, self.root, "commit", "-m", "change app")

    def noop_row(self, target: tuple[str, str], reason: str) -> dict[str, Any]:
        member_id, path = target
        planned = {"action": "noop", "from_ref": self.head(path), "to_ref": REFSPEC, "message": reason}
        return _row(member_id, path, "noop", planned)

    def planned_row(self, target: tuple[str, str]) -> dict[str, Any]:
        member_id, path = target
        planned = {
            "action": "push", "from_ref": self.head(path), "to_ref": REFSPEC, "message": "push to origin",
        }
        return _row(member_id, path, "planned", planned)


def _row(member_id: str, path: str, status: str, planned: dict[str, Any] | None) -> dict[str, Any]:
    return {"member_id": member_id, "member_path": path, "status": status, "planned": planned, "error": None}


def pushed_row(target: tuple[str, str]) -> dict[str, Any]:
    member_id, path = target
    return _row(member_id, path, "ok", None)


def push_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    """The fields these tests pin of each row of a gwz-py `--json` push response."""
    keys = ("member_id", "member_path", "status", "planned", "error")
    return [{key: member[key] for key in keys} for member in response["members"]]


def test_push_of_an_unchanged_workspace_is_noop_with_reasons_and_the_summary_line(
    tmp_path: Path,
) -> None:
    workspace = PublishedWorkspace(tmp_path)

    machine = run_cli(PYTHON_CLI, workspace.root, "--json", "push")
    human = run_cli(PYTHON_CLI, workspace.root, "push")

    assert (machine.returncode, machine.stderr) == (0, ""), machine.stdout
    response = json.loads(machine.stdout)["response"]
    assert response["meta"]["aggregate_status"] == "noop"
    assert response["meta"]["transport"] is None
    assert response["errors"] == []
    assert push_rows(response) == [workspace.noop_row(target, UP_TO_DATE) for target in TARGETS]
    assert (human.returncode, human.stderr) == (0, "")
    assert human.stdout == f"noop\n{THREE_UNCHECKED}\n"


def test_push_check_remotes_reads_every_remote_and_prints_no_summary_line(tmp_path: Path) -> None:
    workspace = PublishedWorkspace(tmp_path)

    machine = run_cli(PYTHON_CLI, workspace.root, "--json", "push", "--check-remotes")
    human = run_cli(PYTHON_CLI, workspace.root, "push", "--check-remotes")

    assert (machine.returncode, machine.stderr) == (0, ""), machine.stdout
    response = json.loads(machine.stdout)["response"]
    assert response["meta"]["aggregate_status"] == "noop"
    assert push_rows(response) == [workspace.noop_row(target, ON_ORIGIN) for target in TARGETS]
    observations = response["meta"]["transport"]
    assert {observation["operation"] for observation in observations} == {"read_advertisement"}
    assert sorted(Path(observation["repository_path"]).resolve() for observation in observations) == sorted(
        (workspace.root / path).resolve() for _, path in TARGETS
    )
    assert workspace.remote_heads() == workspace.heads()
    assert (human.returncode, human.stderr) == (0, "")
    assert human.stdout == "noop\n"


def test_push_after_one_member_and_the_root_changed_publishes_those_two(tmp_path: Path) -> None:
    workspace = PublishedWorkspace(tmp_path)
    workspace.commit_a_change_in_app()
    assert workspace.remote_heads() != workspace.heads()

    machine = run_cli(PYTHON_CLI, workspace.root, "--json", "push")

    assert (machine.returncode, machine.stderr) == (0, ""), machine.stdout
    response = json.loads(machine.stdout)["response"]
    assert response["meta"]["aggregate_status"] == "ok"
    assert push_rows(response) == [
        pushed_row(APP),
        workspace.noop_row(LIB, UP_TO_DATE),
        pushed_row(ROOT),
    ]
    assert workspace.remote_heads() == workspace.heads()
    # `app` and the root are read and pushed; `lib` is read once, as the root's
    # dependency (D10), and not pushed.
    assert sorted(observation["operation"] for observation in response["meta"]["transport"]) == [
        "push", "push", "read_advertisement", "read_advertisement", "read_advertisement",
    ]


def test_push_reports_a_branch_behind_its_last_known_ref_without_contacting_it(
    tmp_path: Path,
) -> None:
    workspace = PublishedWorkspace(tmp_path)
    git(workspace.root / APP[1], "reset", "--hard", "HEAD~1")

    machine = run_cli(PYTHON_CLI, workspace.root, "--json", "push")
    verbose = run_cli(PYTHON_CLI, workspace.root, "--verbose", "push")

    assert (machine.returncode, machine.stderr) == (0, ""), machine.stdout
    response = json.loads(machine.stdout)["response"]
    assert response["meta"]["aggregate_status"] == "noop"
    assert response["meta"]["transport"] is None
    assert push_rows(response) == [
        workspace.noop_row(APP, BEHIND),
        workspace.noop_row(LIB, UP_TO_DATE),
        workspace.noop_row(ROOT, UP_TO_DATE),
    ]
    assert (verbose.returncode, verbose.stderr) == (0, "")
    assert verbose.stdout.splitlines() == [
        "noop",
        THREE_UNCHECKED,
        f"{APP[1]}: {BEHIND}",
        f"{LIB[1]}: {UP_TO_DATE}",
        f".: {UP_TO_DATE}",
    ]


def test_push_dry_run_reports_the_classification_without_contacting_a_remote(
    tmp_path: Path,
) -> None:
    workspace = PublishedWorkspace(tmp_path)
    workspace.commit_a_change_in_app()
    published = workspace.remote_heads()

    machine = run_cli(PYTHON_CLI, workspace.root, "--dry-run", "--json", "push")
    human = run_cli(PYTHON_CLI, workspace.root, "--dry-run", "push")

    assert (machine.returncode, machine.stderr) == (0, ""), machine.stdout
    response = json.loads(machine.stdout)["response"]
    assert response["meta"]["aggregate_status"] == "ok"
    assert response["meta"]["transport"] is None
    # The unchanged row has status `noop` and its reason in a dry run as well.
    assert push_rows(response) == [
        workspace.planned_row(APP),
        workspace.noop_row(LIB, UP_TO_DATE),
        workspace.planned_row(ROOT),
    ]
    assert (human.returncode, human.stderr) == (0, "")
    assert human.stdout == f"ok\n{ONE_UNCHECKED}\n"
    assert workspace.remote_heads() == published


def test_native_bridge_carries_remote_check_to_core_which_then_reads_every_remote(
    tmp_path: Path,
) -> None:
    workspace = PublishedWorkspace(tmp_path)
    client = native_client(workspace.root)

    unset = asyncio.run(client.push())
    changed = asyncio.run(client.push(remote_check=RemoteCheck.changed))
    always = asyncio.run(client.push(remote_check=RemoteCheck.always))

    for response, reason in ((unset, UP_TO_DATE), (changed, UP_TO_DATE), (always, ON_ORIGIN)):
        envelope = response.response
        assert envelope.meta.aggregate_status is AggregateStatus.noop
        assert [
            (row.member_id, row.status, row.planned.action, row.planned.message)
            for row in envelope.members
        ] == [(member_id, MemberStatus.noop, PlannedAction.noop, reason) for member_id, _ in TARGETS]
    assert unset.response.meta.transport is None
    assert changed.response.meta.transport is None
    reads = always.response.meta.transport
    assert reads is not None
    assert {row.operation for row in reads} == {TransportOperation.read_advertisement}
    assert sorted(Path(row.repository_path).resolve() for row in reads) == sorted(
        (workspace.root / path).resolve() for _, path in TARGETS
    )


def test_python_cli_presents_a_root_refused_for_a_missing_dependency(tmp_path: Path) -> None:
    workspace = PublishedWorkspace(tmp_path)
    locked = workspace.head(APP[1])
    # Someone rewinds `app`'s remote below the commit the root lock names. `app`'s
    # last-known ref still records that commit, so a default push does not contact
    # `app`. The root changed, so the push proves its dependencies (D10), finds
    # the commit missing, and refuses the root (§3.6).
    earlier = git(workspace.root / APP[1], "rev-parse", "HEAD~1")
    git(workspace.remote("app"), "update-ref", "refs/heads/main", earlier)
    commit_file(workspace.root, "NOTES.md", "notes\n", "notes")
    published_root = bare_ref(workspace.remote("root"), "refs/heads/main")

    human = run_cli(PYTHON_CLI, workspace.root, "push")
    machine = run_cli(PYTHON_CLI, workspace.root, "--json", "push")

    # gwz-py exits 1 for a rejected aggregate; the Rust CLI exits 2.
    assert (human.returncode, human.stderr) == (1, ""), human.stdout
    assert (machine.returncode, machine.stderr) == (1, ""), machine.stdout
    response = json.loads(machine.stdout)["response"]
    assert response["meta"]["aggregate_status"] == "rejected"
    assert push_rows(response)[:2] == [workspace.noop_row(APP, UP_TO_DATE), workspace.noop_row(LIB, UP_TO_DATE)]
    root = response["members"][2]
    assert (root["member_id"], root["status"], root["planned"]) == ("@root", "rejected", None)
    assert root["error"]["code"] == "remote_rejected"
    refusal = root["error"]["message"]
    for text in (
        f"root publication blocked: cannot prove member mem_app commit {locked} is available",
        "publish the member by pushing a branch that contains the commit",
        "fetch its advertised history and retry",
        "gwz push --check-remotes",
    ):
        assert text in refusal
    assert human.stdout.splitlines() == ["rejected", f".: RemoteRejected: {refusal}", TWO_UNCHECKED]
    assert "push" not in {observation["operation"] for observation in response["meta"]["transport"]}
    assert bare_ref(workspace.remote("root"), "refs/heads/main") == published_root


# Parity: both CLIs on one workspace. `run_tests.py` provides the Rust CLI.


def rust_cli() -> list[str]:
    rust_bin = os.environ.get("GWZ_RUST_BIN")
    if not rust_bin:
        pytest.skip("run_tests.py provides the matching Rust CLI")
    return [rust_bin]


def label(value: str) -> str:
    """One spelling of an enum in either CLI's JSON: `Noop` and `noop`."""
    return value.replace("_", "").lower()


def push_facts(envelope: dict[str, Any]) -> dict[str, Any]:
    """What both CLIs' `--json` push documents must agree on.

    The projections differ by design: the Rust CLI renders the envelope at the top
    level, spells enums as labels (`Noop`) and leaves out `target_kind` and unset
    meta fields; gwz-py wraps the envelope in `response` and spells enums as names
    (`noop`). Concurrent reads are reported in arrival order."""
    meta = envelope["meta"]
    row_keys = (
        "member_id", "member_path", "error", "state", "git_status", "lock_match",
        "lock_difference_reasons", "url_resolution",
    )
    return {
        "action": label(meta["action"]),
        "aggregate_status": label(meta["aggregate_status"]),
        "message": meta.get("message"),
        "errors": envelope["errors"],
        "members": [
            {
                **{key: member[key] for key in row_keys},
                "status": label(member["status"]),
                "source_kind": label(member["source_kind"]),
                "planned": (
                    None
                    if member["planned"] is None
                    else {**member["planned"], "action": label(member["planned"]["action"])}
                ),
            }
            for member in envelope["members"]
        ],
        "transport": sorted(
            (
                (
                    label(observation["operation"]),
                    observation["remote"],
                    str(Path(observation["repository_path"]).resolve()),
                    observation["credential_method"],
                    observation["selection_source"],
                    observation["credential_offered"],
                    observation["authenticated"],
                    observation["public_key_fingerprint"],
                )
                for observation in meta.get("transport") or []
            ),
            key=repr,
        ),
    }


def rust_reasons(stdout: str) -> list[tuple[str, str]]:
    """The `<id> <path> Noop <reason>` rows the Rust CLI prints under `--verbose`."""
    matches = (re.fullmatch(r"(\S+) (\S+) Noop (.+)", line) for line in stdout.splitlines())
    return [(match[2], match[3]) for match in matches if match]


def python_reasons(stdout: str) -> list[tuple[str, str]]:
    """The `<path>: <reason>` lines gwz-py prints under `--verbose`."""
    matches = (re.fullmatch(r"(\S+): (.+)", line) for line in stdout.splitlines())
    return [(match[1], match[2]) for match in matches if match]


def option_help(help_text: str, option: str) -> str:
    """The description of `option` in a CLI's help, with the line wrapping undone."""
    lines = help_text.splitlines()
    for index, line in enumerate(lines):
        match = re.fullmatch(rf"\s+{re.escape(option)}(?:\s\s+(\S.*))?", line)
        if match is None:
            continue
        words = [match[1]] if match[1] else []
        for continuation in lines[index + 1:]:
            if not continuation.strip() or continuation.lstrip().startswith("-"):
                break
            words.append(continuation.strip())
        return " ".join(" ".join(words).split())
    raise AssertionError(f"{option} is not in:\n{help_text}")


def test_both_clis_report_the_same_push_rows_reasons_and_summary_on_one_workspace(
    tmp_path: Path,
) -> None:
    drivers = {"rust": rust_cli(), "python": PYTHON_CLI}
    workspace = PublishedWorkspace(tmp_path)

    for option, reason, summary in (
        ((), UP_TO_DATE, [THREE_UNCHECKED]),
        (("--check-remotes",), ON_ORIGIN, []),
    ):
        documents, human, verbose = {}, {}, {}
        for name, driver in drivers.items():
            documents[name] = json.loads(run_ok(driver, workspace.root, "--json", "push", *option).stdout)
            human[name] = run_ok(driver, workspace.root, "push", *option).stdout
            verbose[name] = run_ok(driver, workspace.root, "--verbose", "push", *option).stdout

        rust = push_facts(documents["rust"])
        assert rust == push_facts(documents["python"]["response"]), option
        assert rust["aggregate_status"] == "noop"
        assert [(member["member_id"], member["planned"]["message"]) for member in rust["members"]] == [
            (member_id, reason) for member_id, _ in TARGETS
        ]
        assert len(rust["transport"]) == (3 if option else 0)
        for name, stdout in human.items():
            lines = [line for line in stdout.splitlines() if "not checked for changes" in line]
            assert lines == summary, (name, option, stdout)
        assert rust_reasons(verbose["rust"]) == python_reasons(verbose["python"]) == [
            (path, reason) for _, path in TARGETS
        ], (option, verbose)
    assert workspace.remote_heads() == workspace.heads()


def test_both_clis_share_the_check_remotes_help_text(tmp_path: Path) -> None:
    descriptions = {}
    for name, driver in (("rust", rust_cli()), ("python", PYTHON_CLI)):
        result = subprocess.run(
            [*driver, "push", "-h"], cwd=tmp_path, check=False, capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, result.stderr
        descriptions[name] = option_help(result.stdout, "--check-remotes")

    assert descriptions == {"rust": CHECK_REMOTES_HELP, "python": CHECK_REMOTES_HELP}
