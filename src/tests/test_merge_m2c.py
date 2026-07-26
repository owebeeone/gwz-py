import asyncio
import json
from pathlib import Path

import pytest

from gwz import cli
from native_helpers import commit_file, git, native_client


@pytest.mark.parametrize("flag", ["--json", "--jsonl"])
def test_native_root_preflight_error_has_root_target_kind(
    flag: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = native_client(tmp_path)
    asyncio.run(client.create_workspace(workspace_id="ws_root_error"))
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "GWZ Test")
    git(tmp_path, "config", "user.email", "gwz@example.invalid")
    git(tmp_path, "add", "gwz.conf")
    git(tmp_path, "commit", "-m", "workspace")

    code = cli.main(
        [
            "--root",
            str(tmp_path),
            flag,
            "--target",
            "@root",
            "merge",
            "feature/missing",
        ]
    )

    assert code == 1
    error = json.loads(capsys.readouterr().out.splitlines()[-1])["errors"][0]
    assert error["member_id"] == "@root"
    assert error["member_path"] == "."
    assert error["target_kind"] == "Root"


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
