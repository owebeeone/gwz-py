"""S3.4 real-workspace acceptance for both public ``gwz log`` clients."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from typing import Any

import pytest


MAGIC_PATHSPEC_CASES = (
    (".", ":(exclude)side-only.txt"),
    (".", ":!side-only.txt"),
    (".", ":^side-only.txt"),
    (":(top)bulk.txt",),
)


@dataclass(frozen=True)
class RealLogWorkspace:
    root: Path
    rust_bin: Path
    env: dict[str, str]
    facts: dict[str, Any]

    def _run(
        self,
        command: list[str],
        *args: str,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [*command, "--root", str(self.root), *args],
            cwd=cwd or self.root,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def run_rust(
        self,
        *args: str,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return self._run([str(self.rust_bin)], *args, cwd=cwd)

    def run_python(
        self,
        *args: str,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return self._run([sys.executable, "-m", "gwz.cli"], *args, cwd=cwd)

    def run_setup(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        result = self.run_rust(*args)
        assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
        return result


def _git(
    repo: Path,
    *args: str,
    env: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
) -> bytes:
    command_env = os.environ.copy()
    command_env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            **(env or {}),
        }
    )
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        env=command_env,
        input=input_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return result.stdout.strip()


def _commit(
    repo: Path,
    relative_path: str,
    content: str,
    message: bytes,
    seconds: int,
    *,
    author_seconds: int | None = None,
    author_name: str = "S3.4 Test",
    author_email: str = "s34@example.test",
) -> str:
    path = repo / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", "--", relative_path)
    timestamp = f"@{seconds} +0000"
    _git(
        repo,
        "commit",
        "-F",
        "-",
        env={
            "GIT_AUTHOR_DATE": f"@{author_seconds if author_seconds is not None else seconds} +0000",
            "GIT_COMMITTER_DATE": timestamp,
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
        },
        input_bytes=message,
    )
    return _git(repo, "rev-parse", "HEAD").decode("ascii")


def _tree(repo: Path, filename: bytes, content: bytes) -> str:
    blob = _git(repo, "hash-object", "-w", "--stdin", input_bytes=content)
    row = b"100644 blob " + blob + b"\t" + filename + b"\0"
    return _git(repo, "mktree", "-z", input_bytes=row).decode("ascii")


def _raw_commit(
    repo: Path,
    ref: str,
    message: bytes,
    seconds: int,
    *,
    tree: str,
    parents: tuple[str, ...] = (),
    author_seconds: int | None = None,
    author_name: bytes = b"S3.4 Test",
    author_email: bytes = b"s34@example.test",
    offset: bytes = b"+0000",
) -> str:
    authored = author_seconds if author_seconds is not None else seconds
    parent_rows = b"".join(f"parent {parent}\n".encode("ascii") for parent in parents)
    identity = author_name + b" <" + author_email + b"> "
    payload = (
        f"tree {tree}\n".encode("ascii")
        + parent_rows
        + b"author "
        + identity
        + str(authored).encode("ascii")
        + b" "
        + offset
        + b"\ncommitter "
        + identity
        + str(seconds).encode("ascii")
        + b" "
        + offset
        + b"\n\n"
        + message
    )
    commit = _git(
        repo,
        "hash-object",
        "--literally",
        "-t",
        "commit",
        "-w",
        "--stdin",
        input_bytes=payload,
    )
    _git(repo, "update-ref", f"refs/heads/{ref}", commit.decode("ascii"))
    return commit.decode("ascii")


def _marker_message(subject: str, marker: str) -> bytes:
    return (
        f"{subject}\n\nGWZ-Commit-ID: {marker}\nGWZ-Workspace-ID: ws_default\n"
    ).encode("ascii")


def _new_workspace(
    root: Path,
    authority: RealLogWorkspace,
    *members: str,
) -> RealLogWorkspace:
    root.mkdir(parents=True)
    workspace = RealLogWorkspace(
        root=root,
        rust_bin=authority.rust_bin,
        env=authority.env,
        facts={},
    )
    workspace.run_setup("--json", "init")
    _git(root, "config", "user.name", "S3.4 Test")
    _git(root, "config", "user.email", "s34@example.test")
    for member in members:
        workspace.run_setup("--json", "repo", "create", f"members/{member}")
        repo = root / "members" / member
        _git(repo, "config", "user.name", "S3.4 Test")
        _git(repo, "config", "user.email", "s34@example.test")
    return workspace


def _parity(
    workspace: RealLogWorkspace,
    *args: str,
    cwd: Path | None = None,
    exit_code: int = 0,
) -> subprocess.CompletedProcess[bytes]:
    rust = workspace.run_rust(*args, cwd=cwd)
    python = workspace.run_python(*args, cwd=cwd)
    _assert_parity_results(workspace, args, exit_code, rust, python)
    return rust


def _assert_parity_results(
    workspace: RealLogWorkspace,
    args: tuple[str, ...],
    exit_code: int,
    rust: subprocess.CompletedProcess[bytes],
    python: subprocess.CompletedProcess[bytes],
) -> None:
    assert rust.args == [
        str(workspace.rust_bin),
        "--root",
        str(workspace.root),
        *args,
    ]
    assert python.args == [
        sys.executable,
        "-m",
        "gwz.cli",
        "--root",
        str(workspace.root),
        *args,
    ]
    assert (rust.returncode, python.returncode) == (exit_code, exit_code)
    assert rust.stdout == python.stdout
    assert rust.stderr == python.stderr


def _machine_records(result: subprocess.CompletedProcess[bytes]) -> list[dict[str, Any]]:
    document = json.loads(result.stdout)
    return document["records"]


def _entries(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [record for record in records if record["record"] == "entry"]


def _member_hashes(records: list[dict[str, Any]], member_id: str) -> list[str]:
    return [
        member["hash"]
        for entry in _entries(records)
        for member in entry["members"]
        if member["member_id"] == member_id
    ]


def test_cross_client_oracle_rejects_collapsed_invocations_and_mismatched_bytes(
    tmp_path: Path,
) -> None:
    workspace = RealLogWorkspace(
        root=tmp_path / "workspace",
        rust_bin=tmp_path / "exact-rust-gwz",
        env={},
        facts={},
    )
    args = ("--json", "log", "-n", "2")
    rust_args = [
        str(workspace.rust_bin),
        "--root",
        str(workspace.root),
        *args,
    ]
    python_args = [
        sys.executable,
        "-m",
        "gwz.cli",
        "--root",
        str(workspace.root),
        *args,
    ]
    rust = subprocess.CompletedProcess(rust_args, 0, b"records\n", b"warning\n")
    python = subprocess.CompletedProcess(python_args, 0, b"records\n", b"warning\n")
    _assert_parity_results(workspace, args, 0, rust, python)

    collapsed_rust = subprocess.CompletedProcess(
        python_args, 0, rust.stdout, rust.stderr
    )
    collapsed_python = subprocess.CompletedProcess(
        rust_args, 0, python.stdout, python.stderr
    )
    stdout_mismatch = subprocess.CompletedProcess(
        python_args, 0, b"different records\n", python.stderr
    )
    stderr_mismatch = subprocess.CompletedProcess(
        python_args, 0, python.stdout, b"different warning\n"
    )
    for mutated_rust, mutated_python in (
        (collapsed_rust, python),
        (rust, collapsed_python),
        (rust, stdout_mismatch),
        (rust, stderr_mismatch),
    ):
        with pytest.raises(AssertionError):
            _assert_parity_results(
                workspace, args, 0, mutated_rust, mutated_python
            )


@pytest.fixture(scope="module")
def real_log_workspace(tmp_path_factory: pytest.TempPathFactory) -> RealLogWorkspace:
    rust = os.environ.get("GWZ_RUST_BIN")
    assert rust, "S3.4 requires GWZ_RUST_BIN pointing at the exact gwz authority"
    rust_bin = Path(rust).resolve()
    assert rust_bin.is_file() and os.access(rust_bin, os.X_OK), rust_bin

    root = tmp_path_factory.mktemp("gwz-log-s34")
    source_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "LC_ALL": "C",
            "PYTHONPATH": str(source_root / "src"),
            "TZ": "Australia/Sydney",
        }
    )
    facts: dict[str, Any] = {}
    workspace = RealLogWorkspace(root=root, rust_bin=rust_bin, env=env, facts=facts)
    workspace.run_setup("--json", "init")
    for member in ("api", "web", "empty"):
        workspace.run_setup("--json", "repo", "create", f"members/{member}")

    for repo in (root, root / "members/api", root / "members/web"):
        _git(repo, "config", "user.name", "S3.4 Test")
        _git(repo, "config", "user.email", "s34@example.test")

    (root / "root.txt").write_text("root\n", encoding="utf-8")
    (root / "members/api/api.txt").write_text("api\n", encoding="utf-8")
    (root / "members/web/web.txt").write_text("web\n", encoding="utf-8")
    _git(root, "add", "root.txt")
    _git(root / "members/api", "add", "api.txt")
    _git(root / "members/web", "add", "web.txt")
    workspace.run_setup(
        "--json",
        "commit",
        "-m",
        "coordinated workspace change",
    )
    api = root / "members/api"
    web = root / "members/web"
    facts["coordinated"] = {
        "@root": _git(root, "rev-parse", "HEAD").decode("ascii"),
        "mem_api": _git(api, "rev-parse", "HEAD").decode("ascii"),
        "mem_web": _git(web, "rev-parse", "HEAD").decode("ascii"),
    }
    coordinated_seconds = max(
        int(_git(repo, "show", "-s", "--format=%ct", "HEAD"))
        for repo in (root, api, web)
    )
    facts["snapshot_pin"] = _commit(
        api,
        "snapshot-pin.txt",
        "snapshot pin\n",
        b"snapshot pin\n",
        coordinated_seconds + 100,
    )
    workspace.run_setup("--json", "snapshot", "baseline")

    base = coordinated_seconds + 1_000
    facts["base_seconds"] = base
    facts["root_after"] = _commit(
        root,
        "root-after.txt",
        "root after\n",
        b"root after\n",
        base + 1,
    )
    facts["web_detached"] = _commit(
        web,
        "detached.txt",
        "detached\n",
        b"detached member history\n",
        base + 2,
    )

    bulk_commits = []
    for index in range(130):
        bulk_commits.append(
            _commit(
                api,
                "bulk.txt",
                f"{index}\n",
                f"bulk {index:02d}\n".encode("ascii"),
                base + 10 + index,
            )
        )
    facts["bulk_commits"] = bulk_commits
    facts["filter_seconds"] = base + 200
    facts["filter_commit"] = _commit(
        api,
        "filter.txt",
        "filter\n",
        b"filter subject\n\nbody token unique\n",
        base + 200,
        author_name="Filter Person",
        author_email="filter@example.test",
    )

    _git(api, "branch", "side")
    facts["main_commit"] = _commit(
        api,
        "main-only.txt",
        "main\n",
        b"main branch\n",
        base + 210,
    )
    _git(api, "checkout", "side")
    facts["side_commit"] = _commit(
        api,
        "side-only.txt",
        "side\n",
        b"side branch\n",
        base + 211,
    )
    _git(api, "checkout", "main")
    merge_timestamp = f"@{base + 212} +0000"
    _git(
        api,
        "merge",
        "--no-ff",
        "side",
        "-m",
        "merge branch",
        env={
            "GIT_AUTHOR_DATE": merge_timestamp,
            "GIT_COMMITTER_DATE": merge_timestamp,
        },
    )
    facts["merge_commit"] = _git(api, "rev-parse", "HEAD").decode("ascii")
    _git(api, "tag", "s34-v1", facts["coordinated"]["mem_api"])
    _git(api, "tag", "s34-v2", facts["merge_commit"])
    _git(root, "tag", "s34-v1", facts["coordinated"]["@root"])
    _git(web, "tag", "s34-v1", facts["coordinated"]["mem_web"])
    _git(web, "checkout", "--detach", facts["web_detached"])

    marker_a = "01987b0c-2f75-7c4a-9a32-8fd22f7d7c91"
    marker_b = "01987b0c-2f75-7c4a-aa32-8fd22f7d7c92"
    api_tree = _tree(api, b"api.txt", b"api\n")
    web_tree = _tree(web, b"web.txt", b"web\n")
    for repo, tree, seconds in ((api, api_tree, base + 200), (web, web_tree, base + 205)):
        _raw_commit(repo, "heuristic", b"heuristic fanout\n", seconds, tree=tree)
    facts["heuristic_must_not"] = {}
    distinct_message = {
        "mem_api": _raw_commit(
            api,
            "heuristic-message",
            b"message alpha\n",
            base + 220,
            tree=api_tree,
        ),
        "mem_web": _raw_commit(
            web,
            "heuristic-message",
            b"message beta\n",
            base + 220,
            tree=web_tree,
        ),
    }
    facts["heuristic_must_not"]["heuristic-message"] = distinct_message
    distinct_author = {
        "mem_api": _raw_commit(
            api,
            "heuristic-author",
            b"same author-sensitive message\n",
            base + 230,
            tree=api_tree,
        ),
        "mem_web": _raw_commit(
            web,
            "heuristic-author",
            b"same author-sensitive message\n",
            base + 230,
            author_name=b"Different Person",
            author_email=b"different@example.test",
            tree=web_tree,
        ),
    }
    facts["heuristic_must_not"]["heuristic-author"] = distinct_author
    distinct_time = {
        "mem_api": _raw_commit(
            api,
            "heuristic-time",
            b"same time-sensitive message\n",
            base + 240,
            author_seconds=base + 240,
            tree=api_tree,
        ),
        "mem_web": _raw_commit(
            web,
            "heuristic-time",
            b"same time-sensitive message\n",
            base + 251,
            author_seconds=base + 240,
            tree=web_tree,
        ),
    }
    facts["heuristic_must_not"]["heuristic-time"] = distinct_time
    same_repo_parent = _raw_commit(
        api,
        "heuristic-same-repo-parent",
        b"same-repo twins\n",
        base + 260,
        tree=api_tree,
    )
    same_repo_child = _raw_commit(
        api,
        "heuristic-same-repo",
        b"same-repo twins\n",
        base + 265,
        parents=(same_repo_parent,),
        tree=api_tree,
    )
    facts["heuristic_must_not"]["heuristic-same-repo"] = {
        "mem_api": [same_repo_child, same_repo_parent]
    }
    _raw_commit(
        api,
        "rebase-case",
        b"rebase restamp\n",
        base + 300,
        author_seconds=base,
        tree=api_tree,
    )
    _raw_commit(
        web,
        "rebase-case",
        b"rebase restamp\n",
        base + 305,
        author_seconds=base + 100,
        tree=web_tree,
    )
    _raw_commit(
        api,
        "marked-boundary",
        _marker_message("marked boundary", marker_a),
        base + 400,
        tree=api_tree,
    )
    _raw_commit(
        web,
        "marked-boundary",
        b"marked boundary\n",
        base + 400,
        tree=web_tree,
    )
    invalid_message = (
        b"invalid marker twins\n\n"
        b"GWZ-Commit-ID = not-a-uuid\nGWZ-Workspace-ID: ws_default\n"
    )
    for repo, tree in ((api, api_tree), (web, web_tree)):
        _raw_commit(repo, "invalid-marker", invalid_message, base + 500, tree=tree)
    filter_marker = "01987b0c-2f75-7c4a-8a32-8fd22f7d7c94"
    _raw_commit(
        api,
        "filtered-marker",
        _marker_message("filtered marker", filter_marker),
        base + 550,
        author_name=b"Filter Person",
        author_email=b"filter@example.test",
        tree=api_tree,
    )
    _raw_commit(
        web,
        "filtered-marker",
        _marker_message("filtered marker", filter_marker),
        base + 550,
        author_name=b"Other Person",
        author_email=b"other@example.test",
        tree=web_tree,
    )
    _raw_commit(
        api,
        "window-boundary",
        _marker_message("inclusive marker", marker_b),
        base + 660,
        tree=api_tree,
    )
    _raw_commit(
        web,
        "window-boundary",
        _marker_message("inclusive marker", marker_b),
        base + 600,
        tree=web_tree,
    )
    frontier_marker = "01987b0c-2f75-7c4a-ba32-8fd22f7d7c93"
    _raw_commit(
        api,
        "frontier",
        _marker_message("frontier api", frontier_marker),
        base + 760,
        tree=api_tree,
    )
    web_late = _raw_commit(
        web,
        "frontier-parent",
        _marker_message("frontier web late", frontier_marker),
        base + 800,
        tree=web_tree,
    )
    _raw_commit(
        web,
        "frontier",
        _marker_message("frontier web old", frontier_marker),
        base + 600,
        parents=(web_late,),
        tree=web_tree,
    )
    tie_seconds = base + 900
    facts["tie_seconds"] = tie_seconds
    _raw_commit(api, "tie", b"tie api\n", tie_seconds, tree=api_tree)
    _raw_commit(web, "tie", b"tie web\n", tie_seconds, tree=web_tree)
    _raw_commit(
        api,
        "extreme",
        b"pre epoch\n",
        -1,
        offset=b"-1200",
        tree=api_tree,
    )
    _raw_commit(
        web,
        "extreme",
        b"far future\n",
        253_402_300_799,
        offset=b"+1400",
        tree=web_tree,
    )
    lossy_tree = _tree(api, b"non-utf8-\xff.txt", b"lossy path\n")
    _raw_commit(
        api,
        "lossy",
        b"lossy \xff \x1b subject\n\nbody \x01 control\n",
        base + 950,
        author_name=b"Author\xff",
        tree=lossy_tree,
    )
    return workspace


def test_actual_gwz_commit_coalesces_across_root_and_members(real_log_workspace) -> None:
    args = ("--json", "log", "--body", "--grep", "coordinated workspace change")
    result = _parity(real_log_workspace, *args)
    entries = _entries(_machine_records(result))
    assert len(entries) == 1
    coordinated = entries[0]
    assert [member["member_id"] for member in coordinated["members"]] == [
        "@root",
        "mem_api",
        "mem_web",
    ]
    assert {
        member["member_id"]: member["hash"] for member in coordinated["members"]
    } == real_log_workspace.facts["coordinated"]
    assert coordinated["provenance"].startswith("marker:")


@pytest.mark.parametrize(
    "mode",
    [
        ("log", "--color", "never"),
        ("log", "--full", "--body", "--color", "never"),
        ("--json", "log", "--body"),
        ("--jsonl", "log", "--body"),
    ],
)
def test_compact_full_json_and_jsonl_match_for_real_history(
    real_log_workspace: RealLogWorkspace,
    mode: tuple[str, ...],
) -> None:
    result = _parity(
        real_log_workspace,
        *mode,
        "--grep",
        "coordinated workspace change",
    )
    assert b"coordinated workspace change" in result.stdout
    if "--json" not in mode and "--jsonl" not in mode:
        assert b"members/api" in result.stdout
        assert b"members/web" in result.stdout
        assert b"gwz log: degraded members/empty" in result.stderr


@pytest.mark.parametrize(
    ("ref", "entry_count", "member_counts", "provenances"),
    [
        ("heuristic", 1, [2], ["heuristic"]),
        ("rebase-case", 2, [1, 1], ["none", "none"]),
        ("marked-boundary", 2, [1, 1], None),
        ("invalid-marker", 2, [1, 1], ["marker-invalid", "marker-invalid"]),
        ("window-boundary", 1, [2], None),
    ],
)
def test_real_marker_and_heuristic_coalescing_matrix(
    real_log_workspace: RealLogWorkspace,
    ref: str,
    entry_count: int,
    member_counts: list[int],
    provenances: list[str] | None,
) -> None:
    result = _parity(
        real_log_workspace,
        "--json",
        "--target",
        "mem_api",
        "--target",
        "mem_web",
        "log",
        "--no-limit",
        ref,
    )
    entries = _entries(_machine_records(result))
    assert len(entries) == entry_count
    assert sorted(len(entry["members"]) for entry in entries) == sorted(member_counts)
    if provenances is not None:
        assert sorted(entry["provenance"] for entry in entries) == sorted(provenances)
    if ref == "marked-boundary":
        assert sorted(entry["provenance"].split(":", 1)[0] for entry in entries) == [
            "marker",
            "none",
        ]
    if ref == "window-boundary":
        assert entries[0]["provenance"].startswith("marker:")


@pytest.mark.parametrize(
    ("ref", "targets"),
    [
        ("heuristic-message", ("mem_api", "mem_web")),
        ("heuristic-author", ("mem_api", "mem_web")),
        ("heuristic-time", ("mem_api", "mem_web")),
        ("heuristic-same-repo", ("mem_api",)),
    ],
)
def test_each_heuristic_must_not_merge_arm_is_independently_real(
    real_log_workspace: RealLogWorkspace,
    ref: str,
    targets: tuple[str, ...],
) -> None:
    target_args = tuple(part for target in targets for part in ("--target", target))
    result = _parity(
        real_log_workspace,
        "--json",
        *target_args,
        "log",
        "--no-limit",
        ref,
    )
    entries = _entries(_machine_records(result))
    expected = real_log_workspace.facts["heuristic_must_not"][ref]
    expected_hashes = {
        commit
        for value in expected.values()
        for commit in (value if isinstance(value, list) else [value])
    }
    assert len(entries) == 2
    assert all(len(entry["members"]) == 1 for entry in entries)
    assert {entry["provenance"] for entry in entries} == {"none"}
    assert {
        member["hash"]
        for entry in entries
        for member in entry["members"]
    } == expected_hashes


def test_no_coalesce_and_selection_narrow_real_marker_groups(
    real_log_workspace: RealLogWorkspace,
) -> None:
    common = ("--grep", "coordinated workspace change")
    raw = _parity(real_log_workspace, "--json", "log", "--no-coalesce", *common)
    raw_entries = _entries(_machine_records(raw))
    assert len(raw_entries) == 3
    assert all(len(entry["members"]) == 1 for entry in raw_entries)

    selected = _parity(
        real_log_workspace,
        "--json",
        "--target",
        "mem_api",
        "log",
        *common,
    )
    selected_entries = _entries(_machine_records(selected))
    assert [member["member_id"] for member in selected_entries[0]["members"]] == [
        "mem_api"
    ]


def test_detached_member_unborn_member_and_strict_status_are_cross_client_exact(
    real_log_workspace: RealLogWorkspace,
) -> None:
    benign = _parity(real_log_workspace, "--json", "log", "-n", "5")
    records = _machine_records(benign)
    assert any(
        record.get("reason") == "unborn" and record.get("member_id") == "mem_empty"
        for record in records
    )

    detached_hash = real_log_workspace.facts["web_detached"]
    detached = _parity(
        real_log_workspace,
        "--json",
        "--target",
        "mem_web",
        "log",
        "--no-limit",
    )
    assert any(
        member["hash"] == detached_hash
        for entry in _entries(_machine_records(detached))
        for member in entry["members"]
    )
    strict = _parity(real_log_workspace, "--json", "log", "-n", "5", "--strict", exit_code=1)
    assert any(record["record"] == "degradation" for record in _machine_records(strict))


def test_unborn_root_is_benign_and_read_failure_is_partial_in_both_clients(
    real_log_workspace: RealLogWorkspace,
    tmp_path: Path,
) -> None:
    unborn = _new_workspace(tmp_path / "unborn-root", real_log_workspace, "solo")
    solo = unborn.root / "members/solo"
    solo_hash = _commit(solo, "solo.txt", "solo\n", b"member only\n", 1_700_000_000)
    result = _parity(unborn, "--json", "log", "--no-limit")
    records = _machine_records(result)
    assert any(
        record.get("reason") == "unborn" and record.get("member_id") == "@root"
        for record in records
    )
    assert any(
        member["hash"] == solo_hash
        for entry in _entries(records)
        for member in entry["members"]
    )

    unreadable = _new_workspace(tmp_path / "unreadable-member", real_log_workspace, "gone")
    (unreadable.root / "root.txt").write_text("root\n", encoding="utf-8")
    (unreadable.root / "members/gone/gone.txt").write_text("gone\n", encoding="utf-8")
    _git(unreadable.root, "add", "root.txt")
    _git(unreadable.root / "members/gone", "add", "gone.txt")
    unreadable.run_setup("--json", "commit", "-m", "before member disappears")
    (unreadable.root / "members/gone").rename(unreadable.root / "members/gone-away")
    partial = _parity(unreadable, "--json", "log", "--no-limit", exit_code=1)
    assert any(
        record.get("reason") == "repository_unreadable"
        and record.get("member_id") == "mem_gone"
        for record in _machine_records(partial)
    )


@pytest.mark.parametrize(
    ("operand", "reason", "pin_fact"),
    [
        ("+baseline..HEAD", "snapshot_entry_missing", "snapshot_pin"),
        ("+lock..HEAD", "lock_entry_missing", "coordinated"),
    ],
)
def test_snapshot_and_lock_ranges_auto_lift_with_exact_post_pin_histories(
    real_log_workspace: RealLogWorkspace,
    operand: str,
    reason: str,
    pin_fact: str,
) -> None:
    result = _parity(real_log_workspace, "--json", "log", operand)
    records = _machine_records(result)
    assert any(
        record.get("member_id") == "@root" and record.get("reason") == reason
        for record in records
    )
    pins = real_log_workspace.facts[pin_fact]
    if isinstance(pins, str):
        pins = {
            "mem_api": pins,
            "mem_web": real_log_workspace.facts["coordinated"]["mem_web"],
        }
    for member_id, member_name in (("mem_api", "api"), ("mem_web", "web")):
        pin = pins[member_id]
        expected = _git(
            real_log_workspace.root / "members" / member_name,
            "rev-list",
            f"{pin}..HEAD",
        ).decode("ascii").splitlines()
        actual = _member_hashes(records, member_id)
        assert actual == expected
        assert pin not in actual
    api_hashes = _member_hashes(records, "mem_api")
    assert len(api_hashes) > 50
    if pin_fact == "snapshot_pin":
        assert real_log_workspace.facts["coordinated"]["mem_api"] not in api_hashes


def test_tagged_range_and_explicit_range_narrow_the_real_selection(
    real_log_workspace: RealLogWorkspace,
) -> None:
    tagged = _parity(
        real_log_workspace,
        "--json",
        "log",
        "--tagged",
        "--no-limit",
        "s34-v1..s34-v2",
    )
    tagged_entries = _entries(_machine_records(tagged))
    assert tagged_entries
    assert {
        member["member_id"]
        for entry in tagged_entries
        for member in entry["members"]
    } == {"mem_api"}

    first_bulk = real_log_workspace.facts["bulk_commits"][0]
    ranged = _parity(
        real_log_workspace,
        "--json",
        "--target",
        "mem_api",
        "log",
        f"{first_bulk}..HEAD",
    )
    hashes = _member_hashes(_machine_records(ranged), "mem_api")
    expected = _git(
        real_log_workspace.root / "members/api",
        "rev-list",
        f"{first_bulk}..HEAD",
    ).decode("ascii").splitlines()
    assert hashes == expected
    assert len(hashes) > 50
    assert first_bulk not in hashes
    assert real_log_workspace.facts["merge_commit"] in hashes


def test_workspace_and_member_cwd_pathspec_routing_matches_native_git_magic(
    real_log_workspace: RealLogWorkspace,
) -> None:
    root_view = _parity(
        real_log_workspace,
        "--json",
        "log",
        "--no-limit",
        "--",
        "members/api/bulk.txt",
    )
    member_view = _parity(
        real_log_workspace,
        "--json",
        "log",
        "--no-limit",
        "--",
        "bulk.txt",
        cwd=real_log_workspace.root / "members/api",
    )
    assert root_view.stdout == member_view.stdout
    assert {
        member["member_id"]
        for entry in _entries(_machine_records(root_view))
        for member in entry["members"]
    } == {"mem_api"}
    api = real_log_workspace.root / "members/api"
    assert _member_hashes(_machine_records(root_view), "mem_api") == _git(
        api, "rev-list", "HEAD", "--", "bulk.txt"
    ).decode("ascii").splitlines()

    for pathspecs in MAGIC_PATHSPEC_CASES:
        result = _parity(
            real_log_workspace,
            "--json",
            "log",
            "--no-limit",
            "--",
            *pathspecs,
            cwd=api,
        )
        actual = _member_hashes(_machine_records(result), "mem_api")
        expected = _git(api, "rev-list", "HEAD", "--", *pathspecs).decode(
            "ascii"
        ).splitlines()
        assert actual == expected


def test_native_git_magic_matrix_carries_both_short_exclusion_aliases() -> None:
    assert (".", ":!side-only.txt") in MAGIC_PATHSPEC_CASES
    assert (".", ":^side-only.txt") in MAGIC_PATHSPEC_CASES


def test_all_six_filters_and_marker_survivor_are_cross_client_exact(
    real_log_workspace: RealLogWorkspace,
) -> None:
    filter_seconds = real_log_workspace.facts["filter_seconds"]
    api = real_log_workspace.root / "members/api"
    filter_hash = real_log_workspace.facts["filter_commit"]
    cases = [
        (("--grep", "body token unique"), [filter_hash]),
        (("--author", r"Filter Person <filter@example\.test>"), [filter_hash]),
        (
            ("--since", f"@{filter_seconds}", "--until", f"@{filter_seconds}"),
            [filter_hash],
        ),
        (
            ("--no-merges",),
            _git(api, "rev-list", "--no-merges", "HEAD").decode("ascii").splitlines(),
        ),
        (
            ("--first-parent",),
            _git(api, "rev-list", "--first-parent", "HEAD")
            .decode("ascii")
            .splitlines(),
        ),
    ]
    for flags, expected in cases:
        result = _parity(
            real_log_workspace,
            "--json",
            "--target",
            "mem_api",
            "log",
            "--no-limit",
            *flags,
        )
        assert _member_hashes(_machine_records(result), "mem_api") == expected

    survivor = _parity(
        real_log_workspace,
        "--json",
        "--target",
        "mem_api",
        "--target",
        "mem_web",
        "log",
        "--no-limit",
        "--author",
        "^Filter Person",
        "filtered-marker",
    )
    survivor_entries = _entries(_machine_records(survivor))
    assert len(survivor_entries) == 1
    assert [member["member_id"] for member in survivor_entries[0]["members"]] == [
        "mem_api"
    ]
    assert survivor_entries[0]["provenance"].startswith("marker:")


@pytest.mark.parametrize(
    ("args", "teaching"),
    [
        (("log", "--grep", "["), b"regex"),
        (("log", "--since", "yesterday"), b"RFC3339"),
    ],
)
def test_invalid_regex_and_approxidate_are_process_rejections(
    real_log_workspace: RealLogWorkspace,
    args: tuple[str, ...],
    teaching: bytes,
) -> None:
    for result in (
        real_log_workspace.run_rust(*args),
        real_log_workspace.run_python(*args),
    ):
        assert result.returncode == 2
        assert result.stdout == b""
        assert b"InvalidRequest" in result.stderr
        assert teaching in result.stderr

    empty = _parity(
        real_log_workspace,
        "--target",
        "mem_api",
        "log",
        "--grep",
        "no such subject anywhere",
    )
    assert empty.stdout == empty.stderr == b""


def test_default_explicit_zero_and_filter_lift_depths_have_exact_hashes(
    real_log_workspace: RealLogWorkspace,
) -> None:
    prefix = ("--json", "--target", "mem_api", "log")
    default = _parity(real_log_workspace, *prefix)
    limited = _parity(real_log_workspace, *prefix, "-n", "2")
    zero = _parity(real_log_workspace, *prefix, "-n", "0")
    unlimited = _parity(real_log_workspace, *prefix, "--no-limit")
    since_lifted = _parity(
        real_log_workspace,
        *prefix,
        "--since",
        f"@{real_log_workspace.facts['base_seconds']}",
    )
    until_lifted = _parity(
        real_log_workspace,
        *prefix,
        "--until",
        f"@{real_log_workspace.facts['filter_seconds']}",
    )
    api = real_log_workspace.root / "members/api"
    timestamped = [
        row.split(" ", 1)
        for row in _git(api, "rev-list", "--timestamp", "HEAD")
        .decode("ascii")
        .splitlines()
    ]
    full = [commit for _, commit in timestamped]
    since_expected = [
        commit
        for seconds, commit in timestamped
        if int(seconds) >= real_log_workspace.facts["base_seconds"]
    ]
    until_expected = [
        commit
        for seconds, commit in timestamped
        if int(seconds) <= real_log_workspace.facts["filter_seconds"]
    ]
    assert _member_hashes(_machine_records(default), "mem_api") == full[:50]
    assert _member_hashes(_machine_records(limited), "mem_api") == full[:2]
    assert _member_hashes(_machine_records(zero), "mem_api") == full
    assert _member_hashes(_machine_records(unlimited), "mem_api") == full
    assert _member_hashes(_machine_records(since_lifted), "mem_api") == since_expected
    assert _member_hashes(_machine_records(until_lifted), "mem_api") == until_expected
    assert len(full) > 50
    assert len(since_expected) > 50
    assert len(until_expected) > 50


def test_equal_timestamp_order_is_byte_identical_across_jobs(
    real_log_workspace: RealLogWorkspace,
) -> None:
    outputs = []
    for jobs in (1, 2, 4):
        result = _parity(
            real_log_workspace,
            "--jsonl",
            "--jobs",
            str(jobs),
            "--target",
            "mem_api",
            "--target",
            "mem_web",
            "log",
            "--no-limit",
            "--no-coalesce",
            "tie",
        )
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1] == outputs[2]
    records = [json.loads(line) for line in outputs[0].splitlines()[1:]]
    assert [record["members"][0]["member_id"] for record in records] == [
        "mem_api",
        "mem_web",
    ]


def test_non_monotone_frontier_fragments_without_retroactive_merge(
    real_log_workspace: RealLogWorkspace,
) -> None:
    result = _parity(
        real_log_workspace,
        "--json",
        "--target",
        "mem_api",
        "--target",
        "mem_web",
        "log",
        "--no-limit",
        "frontier",
    )
    entries = _entries(_machine_records(result))
    assert [entry["subject"] for entry in entries] == [
        "frontier api",
        "frontier web old",
        "frontier web late",
    ]
    assert len({entry["provenance"] for entry in entries}) == 1
    assert entries[0]["provenance"].startswith("marker:")


def test_pre_epoch_far_future_and_non_utf8_c0_history_match(
    real_log_workspace: RealLogWorkspace,
) -> None:
    extreme_args = (
        "--target",
        "mem_api",
        "--target",
        "mem_web",
        "log",
        "--no-limit",
        "extreme",
    )
    machine = _parity(real_log_workspace, "--json", *extreme_args)
    entries = _entries(_machine_records(machine))
    assert [entry["subject"] for entry in entries] == ["far future", "pre epoch"]
    assert [entry["committer"]["time"]["time"] for entry in entries] == [
        253_402_300_799,
        -1,
    ]
    assert [entry["committer"]["time"]["offset_min"] for entry in entries] == [
        840,
        -720,
    ]
    human = _parity(real_log_workspace, *extreme_args, "--color", "never")
    assert b"pre epoch" in human.stdout and b"far future" in human.stdout
    assert b"-1200" in human.stdout and b"+1400" in human.stdout

    lossy_machine_args = (
        "--target",
        "mem_api",
        "log",
        "--no-limit",
        "--body",
        "lossy",
        "--",
        ".",
    )
    lossy_machine = _parity(real_log_workspace, "--json", *lossy_machine_args)
    lossy_entry = _entries(_machine_records(lossy_machine))[0]
    assert b"non-utf8-\xff.txt" in _git(
        real_log_workspace.root / "members/api", "ls-tree", "-z", "lossy"
    )
    assert lossy_entry["lossy"] is True
    assert b"\\u001b" in lossy_machine.stdout
    assert b"\\u0001" in lossy_machine.stdout
    assert b"\x1b" not in lossy_machine.stdout
    lossy_human = _parity(
        real_log_workspace,
        "--target",
        "mem_api",
        "log",
        "--no-limit",
        "--full",
        "--body",
        "--color",
        "never",
        "lossy",
        "--",
        ".",
    )
    assert b"\xef\xbf\xbd" in lossy_human.stdout
    assert b"\x1b" not in lossy_human.stdout
    assert b"\x01" not in lossy_human.stdout
    assert b"    body \xef\xbf\xbd control\n" in lossy_human.stdout


def _preclosed_stdout_process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> tuple[int, bytes]:
    read_descriptor, write_descriptor = os.pipe()
    os.close(read_descriptor)
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=write_descriptor,
            stderr=subprocess.PIPE,
        )
    finally:
        os.close(write_descriptor)
    _, stderr = process.communicate(timeout=10)
    return process.returncode, stderr


@pytest.mark.parametrize("machine", [False, True])
def test_preclosed_real_stdout_epipe_exits_cleanly_for_both_clients(
    real_log_workspace: RealLogWorkspace,
    machine: bool,
) -> None:
    args = [
        "--root",
        str(real_log_workspace.root),
        "--target",
        "mem_api",
    ]
    if machine:
        args.append("--jsonl")
    args.extend(["log", "--no-limit"])
    commands = (
        [str(real_log_workspace.rust_bin), *args],
        [sys.executable, "-m", "gwz.cli", *args],
    )
    for command in commands:
        code, stderr = _preclosed_stdout_process(
            command,
            cwd=real_log_workspace.root,
            env=real_log_workspace.env,
        )
        assert code == 0
        assert stderr == b""


_TRACKED_EPIPE_CHILD = r"""
import os
import socket
import sys

import gwz.cli as cli
import gwz.cli_log as cli_log
from gwz.client import Client

control = socket.create_connection(
    ("127.0.0.1", int(os.environ["GWZ_S34_CONTROL_PORT"]))
)
target = int(os.environ["GWZ_S34_BREAK_WRITE"])

original_release = Client._release_log_output

async def tracked_release(self, log_ref):
    try:
        return await original_release(self, log_ref)
    finally:
        control.sendall(b"L\n")

Client._release_log_output = tracked_release

if target:
    original_write = cli_log._write_and_flush
    write_count = 0
    triggered = False

    def tracked_write(stream, value):
        global write_count, triggered
        write_count += 1
        if triggered:
            control.sendall(b"X\n")
        if write_count == target:
            triggered = True
            control.sendall(b"R\n")
            control.recv(1)
        return original_write(stream, value)

    cli_log._write_and_flush = tracked_write

code = cli.main(sys.argv[1:])
control.sendall(f"C:{code}\n".encode("ascii"))
raise SystemExit(code)
"""


def _read_control_line(control: socket.socket) -> bytes:
    row = bytearray()
    while not row.endswith(b"\n"):
        chunk = control.recv(1)
        assert chunk, "EPIPE child exited before reaching its synchronized write"
        row.extend(chunk)
    return bytes(row)


def _tracked_python_epipe(
    workspace: RealLogWorkspace,
    args: tuple[str, ...],
    *,
    break_write: int,
) -> tuple[int, bytes, bytes]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(10)
    child_env = workspace.env.copy()
    child_env.update(
        {
            "GWZ_S34_CONTROL_PORT": str(listener.getsockname()[1]),
            "GWZ_S34_BREAK_WRITE": str(break_write),
        }
    )
    command = [
        sys.executable,
        "-c",
        _TRACKED_EPIPE_CHILD,
        "--root",
        str(workspace.root),
        *args,
    ]
    if break_write == 0:
        try:
            code, stderr = _preclosed_stdout_process(
                command,
                cwd=workspace.root,
                env=child_env,
            )
        finally:
            control, _ = listener.accept()
            listener.close()
        control.settimeout(10)
        received = b""
        while chunk := control.recv(4096):
            received += chunk
        control.close()
        return code, stderr, received

    process = subprocess.Popen(
        command,
        cwd=workspace.root,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    control, _ = listener.accept()
    listener.close()
    control.settimeout(10)
    assert process.stdout is not None
    received = b""
    while not received.endswith(b"R\n"):
        row = _read_control_line(control)
        assert row in {b"L\n", b"R\n"}
        received += row
    process.stdout.close()
    process.stdout = None
    control.sendall(b"G")
    _, stderr = process.communicate(timeout=10)
    while chunk := control.recv(4096):
        received += chunk
    control.close()
    return process.returncode, stderr, received


@pytest.mark.parametrize(
    ("args", "break_write"),
    [
        (("--jsonl", "--target", "mem_api", "log", "-n", "2"), 0),
        (("--jsonl", "--target", "mem_api", "log", "-n", "2"), 2),
        (
            (
                "--json",
                "--target",
                "mem_api",
                "log",
                "--grep",
                "body token unique",
            ),
            3,
        ),
        (("--target", "mem_api", "log", "-n", "2", "--color", "never"), 2),
    ],
)
def test_python_epipe_prefix_record_suffix_and_later_human_are_synchronized(
    real_log_workspace: RealLogWorkspace,
    args: tuple[str, ...],
    break_write: int,
) -> None:
    code, stderr, control = _tracked_python_epipe(
        real_log_workspace,
        args,
        break_write=break_write,
    )
    assert code == 0
    assert stderr == b""
    assert control.count(b"L\n") == 1
    assert b"X\n" not in control
    assert control.endswith(b"C:0\n")
