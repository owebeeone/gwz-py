from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from gwz import Client
from gwz.bridge import NativeCoreBridge


def native_module():
    return pytest.importorskip("gwz._gwz_core")


def native_client(root: Path) -> Client:
    return Client(root=root, bridge=NativeCoreBridge(native=native_module()))


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout.strip()


def create_git_repo(repo: Path) -> str:
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", str(repo)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    git(repo, "checkout", "-B", "main")
    git(repo, "config", "user.name", "GWZ Test")
    git(repo, "config", "user.email", "gwz@example.invalid")
    (repo / "README.md").write_text("one\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-m", "initial")
    return git(repo, "rev-parse", "HEAD")


def commit_file(repo: Path, relative_path: str, content: str, message: str) -> str:
    git(repo, "config", "user.name", "GWZ Test")
    git(repo, "config", "user.email", "gwz@example.invalid")
    (repo / relative_path).write_text(content, encoding="utf-8")
    git(repo, "add", relative_path)
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def create_workspace_with_member(root: Path) -> tuple[Path, str]:
    client = native_client(root)
    asyncio.run(client.create_workspace(workspace_id="ws_native"))
    asyncio.run(client.create_repo("repos/app", member_id="mem_app", source_id="src_app"))
    repo = root / "repos" / "app"
    commit = commit_file(repo, "README.md", "one\n", "initial")
    asyncio.run(client.capture(paths=["repos/app"]))
    return repo, commit


def init_bare_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "--bare", str(repo)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def bare_ref(repo: Path, ref: str) -> str | None:
    result = subprocess.run(
        ["git", "--git-dir", str(repo), "rev-parse", "--verify", ref],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()
