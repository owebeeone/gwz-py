#!/usr/bin/env python3
"""Reconcile the gwz-py ``release`` branch for a new release.

gwz-py ships from a ``release`` branch that differs from ``main`` in the same
way as gwz-cli: on ``main`` the native crate depends on a sibling
``../gwz-core`` checkout, while on ``release`` it depends on ``gwz-core`` via a
pinned ``git`` + ``tag`` source.

For a release tag ``vX.Y.Z`` this script:

  1. Verifies the matching gwz-core tag exists at the release branch's
     configured gwz-core git URL.
  2. Creates a temporary worktree on ``release`` and merges ``main`` into it.
  3. Sets the gwz-py Cargo package version to ``X.Y.Z`` and pins the gwz-core
     dependency tag to ``vX.Y.Z``.
  4. Checks protocol drift against gwz-core at the same tag, runs cargo/Python
     tests, builds an installable wheel, and smoke-tests the installed
     ``gwz-py`` command.
  5. Commits the reconciled release branch and creates the lightweight
     ``vX.Y.Z`` tag without ever moving an existing tag.

The release branch advances only after all checks pass. Pushing is explicit via
``--push``. For the first release, pass ``--bootstrap-release`` to create the
``release`` branch from ``main`` and initialize its git-pinned ``gwz-core``
dependency before cutting the tag.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_GWZ_CORE_URL = "https://github.com/owebeeone/gwz-core"


def fail(message: str) -> None:
    print(f"release: error: {message}", file=sys.stderr)
    raise SystemExit(1)


def log(message: str) -> None:
    print(f"release: {message}")


def run(
    cmd: list[object],
    *,
    cwd: Path | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    printable = " ".join(str(part) for part in cmd)
    log(f"$ {printable}")
    result = subprocess.run(
        [str(part) for part in cmd],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=capture,
        text=True,
    )
    if check and result.returncode != 0:
        if capture and result.stdout:
            print(result.stdout, file=sys.stderr)
        if capture and result.stderr:
            print(result.stderr, file=sys.stderr)
        fail(f"command failed ({result.returncode}): {printable}")
    return result


def git(args: list[object], **kwargs: object) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", REPO, *args], **kwargs)


def git_wt(
    worktree: Path,
    args: list[object],
    **kwargs: object,
) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", worktree, *args], **kwargs)


def branch_exists(branch: str) -> bool:
    return (
        git(["rev-parse", "--verify", "--quiet", branch], capture=True, check=False).returncode
        == 0
    )


def tag_commit(tag: str) -> str | None:
    existing = git(
        ["rev-parse", "-q", "--verify", f"refs/tags/{tag}^{{commit}}"],
        capture=True,
        check=False,
    )
    if existing.returncode != 0:
        return None
    return existing.stdout.strip()


def require_clean_worktree() -> None:
    status = git(["status", "--porcelain"], capture=True).stdout.strip()
    if status:
        fail(
            "working tree is not clean; commit or stash changes before releasing. "
            "The release branch is created from committed refs, not from the "
            "current index or working tree."
        )


def is_ancestor(ancestor: str, descendant: str) -> bool:
    return (
        git(
            ["merge-base", "--is-ancestor", ancestor, descendant],
            capture=True,
            check=False,
        ).returncode
        == 0
    )


def warn_if_behind_upstream(branch: str) -> None:
    upstream = git(
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", f"{branch}@{{u}}"],
        capture=True,
        check=False,
    )
    if upstream.returncode != 0 or not upstream.stdout.strip():
        return
    name = upstream.stdout.strip()
    behind = git(["rev-list", "--count", f"{branch}..{name}"], capture=True, check=False)
    count = behind.stdout.strip()
    if count and count != "0":
        log(
            f"WARNING: local {branch} is {count} commit(s) behind {name}; "
            f"releasing local {branch}"
        )


def gwz_core_url(release: str) -> str:
    toml = git(["show", f"{release}:Cargo.toml"], capture=True).stdout
    match = re.search(r'gwz-core\s*=\s*\{[^}\n]*\bgit\s*=\s*"([^"]+)"', toml)
    if not match:
        fail(f"no git-sourced gwz-core dependency found on branch '{release}'")
    return match.group(1)


def verify_remote_tag(url: str, tag: str) -> None:
    result = run(["git", "ls-remote", "--tags", url, f"refs/tags/{tag}"], capture=True)
    if not result.stdout.strip():
        fail(f"gwz-core tag {tag} not found at {url}; release gwz-core first")
    log(f"verified gwz-core {tag} exists at {url}")


def release_branch_is_free(release: str) -> None:
    git(["worktree", "prune"], check=False)
    porcelain = git(["worktree", "list", "--porcelain"], capture=True).stdout
    if re.search(rf"^branch refs/heads/{re.escape(release)}$", porcelain, re.M):
        fail(
            f"branch '{release}' is checked out in another worktree; free it "
            "before running the release script"
        )


def merge_head_exists(worktree: Path) -> bool:
    return (
        git_wt(
            worktree,
            ["rev-parse", "-q", "--verify", "MERGE_HEAD"],
            capture=True,
            check=False,
        ).returncode
        == 0
    )


def do_merge(worktree: Path, main: str, release: str) -> None:
    already = is_ancestor(main, release)
    merge = git_wt(worktree, ["merge", "--no-ff", "--no-commit", main], capture=True, check=False)
    conflicts = [
        path
        for path in git_wt(
            worktree,
            ["diff", "--name-only", "--diff-filter=U"],
            capture=True,
        ).stdout.split()
        if path
    ]
    if conflicts:
        other = [path for path in conflicts if path != "Cargo.lock"]
        if other:
            git_wt(worktree, ["merge", "--abort"], check=False)
            fail("merge conflicts beyond Cargo.lock:\n  " + "\n  ".join(other))
        git_wt(worktree, ["checkout", "--theirs", "--", "Cargo.lock"], check=False)
        git_wt(worktree, ["add", "Cargo.lock"])
        log("resolved Cargo.lock merge conflict; cargo check will refresh it")
    elif not merge_head_exists(worktree):
        if already:
            log(f"{release} already contains {main}; reconciling release metadata only")
        else:
            git_wt(worktree, ["merge", "--abort"], check=False)
            fail(f"`git merge {main}` did not produce a merge:\n{merge.stdout}{merge.stderr}")


def reconcile_cargo_toml(
    worktree: Path,
    tag: str,
    version: str,
    *,
    core_url: str | None = None,
) -> bool:
    path = worktree / "Cargo.toml"
    text = path.read_text(encoding="utf-8")
    updated = re.sub(
        r'^(version\s*=\s*)"[^"]*"',
        rf'\g<1>"{version}"',
        text,
        count=1,
        flags=re.M,
    )
    if core_url is None:
        updated = re.sub(
            r'(gwz-core\s*=\s*\{[^}\n]*\btag\s*=\s*)"[^"]*"',
            rf'\g<1>"{tag}"',
            updated,
        )
    else:
        dependency = f'gwz-core = {{ git = "{core_url}", tag = "{tag}" }}'
        updated, count = re.subn(
            r'^gwz-core\s*=\s*\{[^}\n]*\}\s*$',
            dependency,
            updated,
            count=1,
            flags=re.M,
        )
        if count != 1:
            fail("Cargo.toml did not contain exactly one gwz-core dependency line")
    if updated == text:
        if f'version = "{version}"' in updated and f'tag = "{tag}"' in updated:
            log("Cargo.toml already has matching version and gwz-core tag")
            return False
        fail("Cargo.toml did not contain the expected version and gwz-core git tag fields")
    if f'version = "{version}"' not in updated or f'tag = "{tag}"' not in updated:
        fail(f"Cargo.toml reconcile did not yield version={version} and gwz-core tag={tag}")
    path.write_text(updated, encoding="utf-8", newline="\n")
    log(f"reconciled Cargo.toml: version = {version}, gwz-core tag = {tag}")
    return True


def verify_console_script(worktree: Path) -> None:
    pyproject = (worktree / "pyproject.toml").read_text(encoding="utf-8")
    if 'gwz-py = "gwz.cli:main"' not in pyproject:
        fail("pyproject.toml must install the Python CLI as `gwz-py`")


def verify_locked_git_pin(worktree: Path, tag: str) -> None:
    lock = (worktree / "Cargo.lock").read_text(encoding="utf-8")
    match = re.search(
        r'\[\[package\]\]\nname = "gwz-core"\nversion = "[^"]*"\n(?:source = "([^"]+)"\n)?',
        lock,
    )
    source = match.group(1) if match else None
    if not source or "git+" not in source or f"tag={tag}" not in source:
        fail(
            f"Cargo.lock does not pin gwz-core via git tag {tag} "
            f"(source={source!r})"
        )
    log(f"verified Cargo.lock pins gwz-core via {source}")


def checkout_gwz_core(url: str, tag: str, target: Path) -> None:
    run(["git", "clone", "--depth", "1", "--branch", tag, url, target])


def run_release_checks(
    worktree: Path,
    args: argparse.Namespace,
    *,
    tag: str,
    version: str,
) -> None:
    verify_console_script(worktree)
    run([sys.executable, "scripts/check_protocol_drift.py"], cwd=worktree)
    run([sys.executable, "scripts/regen_protocol.py", "--check"], cwd=worktree)
    run(["cargo", "check"], cwd=worktree)
    verify_locked_git_pin(worktree, tag)
    if not args.no_test:
        run([sys.executable, "run_tests.py"], cwd=worktree)
    if not args.no_package_smoke:
        run(
            [
                sys.executable,
                "scripts/package_smoke.py",
                "--expected-version",
                version,
            ],
            cwd=worktree,
        )


def ensure_tag(tag: str, target: str) -> None:
    existing = tag_commit(tag)
    if existing is not None:
        if existing == target:
            log(f"tag {tag} already points at {target[:10]}; leaving it")
            return
        fail(
            f"tag {tag} already exists at {existing[:10]}, "
            f"not release commit {target[:10]}; refusing to move it"
        )
    git(["tag", tag, target])
    log(f"created tag {tag} -> {target[:10]}")


def push_release(release: str, tag: str) -> None:
    result = run(
        ["git", "-C", REPO, "push", "--atomic", "origin", release, tag],
        capture=True,
        check=False,
    )
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        fail(f"`git push --atomic origin {release} {tag}` failed")
    log(f"pushed {release} and {tag} to origin")


def bootstrap_release(args: argparse.Namespace, tag: str, version: str) -> int:
    if branch_exists(args.release):
        fail(
            f"branch '{args.release}' already exists; omit --bootstrap-release "
            "for normal releases"
        )
    existing = tag_commit(tag)
    if existing is not None:
        fail(f"tag {tag} already exists at {existing[:10]}; refusing to bootstrap")

    core_url = args.gwz_core_url
    verify_remote_tag(core_url, tag)

    temp_root = Path(tempfile.mkdtemp(prefix=f"gwz-py-{tag}-bootstrap-"))
    worktree = temp_root / "gwz-py"
    core_checkout = temp_root / "gwz-core"
    committed = False

    git(["branch", args.release, args.main])
    try:
        release_branch_is_free(args.release)
        git(["worktree", "add", worktree, args.release])
        reconcile_cargo_toml(worktree, tag, version, core_url=core_url)
        checkout_gwz_core(core_url, tag, core_checkout)
        run_release_checks(worktree, args, tag=tag, version=version)
        git_wt(worktree, ["add", "Cargo.toml", "Cargo.lock"])
        staged = git_wt(
            worktree,
            ["diff", "--cached", "--name-only"],
            capture=True,
        ).stdout.split()
        if not staged:
            fail("bootstrap produced no release metadata changes")
        message = f"chore(release): initialize gwz-py {version} (pins gwz-core {tag})"
        git_wt(worktree, ["commit", "-m", message])
        committed = True
        target = git_wt(worktree, ["rev-parse", "HEAD"], capture=True).stdout.strip()
        log(f"{args.release} initialized -> {target[:10]} (gwz-py {version}, gwz-core {tag})")
        ensure_tag(tag, target)
        if args.push:
            push_release(args.release, tag)
        else:
            log("next step (not done without --push):")
            log(f"  git -C {REPO} push origin {args.release} {tag}")
        return 0
    finally:
        if args.keep_worktree:
            log(f"left worktree at {worktree}")
        else:
            git(["worktree", "remove", "--force", worktree], check=False)
            git(["worktree", "prune"], check=False)
            shutil.rmtree(temp_root, ignore_errors=True)
            if not committed:
                git(["branch", "-D", args.release], check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", help="release tag, e.g. v0.3.0")
    parser.add_argument("--main", default="main", help="source branch to merge from")
    parser.add_argument("--release", default="release", help="release branch to reconcile")
    parser.add_argument(
        "--bootstrap-release",
        action="store_true",
        help="create --release from --main for the first release",
    )
    parser.add_argument(
        "--gwz-core-url",
        default=DEFAULT_GWZ_CORE_URL,
        help="gwz-core git URL used when bootstrapping the release branch",
    )
    parser.add_argument("--no-test", action="store_true", help="skip python run_tests.py")
    parser.add_argument("--no-package-smoke", action="store_true", help="skip wheel smoke test")
    parser.add_argument("--push", action="store_true", help="push release branch and tag")
    parser.add_argument("--keep-worktree", action="store_true", help="preserve temp worktree")
    args = parser.parse_args()

    tag = args.tag
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        fail(f"tag must look like vX.Y.Z, got {tag!r}")
    version = tag[1:]

    for tool in ("git", "cargo"):
        if shutil.which(tool) is None:
            fail(f"`{tool}` not found on PATH")
    require_clean_worktree()
    if not branch_exists(args.main):
        fail(f"branch '{args.main}' does not exist in {REPO}")
    if args.bootstrap_release:
        return bootstrap_release(args, tag, version)
    if not branch_exists(args.release):
        fail(
            f"branch '{args.release}' does not exist in {REPO}; "
            "run with --bootstrap-release for the first release"
        )

    release_branch_is_free(args.release)
    warn_if_behind_upstream(args.main)
    warn_if_behind_upstream(args.release)

    core_url = gwz_core_url(args.release)
    verify_remote_tag(core_url, tag)

    existing = tag_commit(tag)
    if existing is not None:
        head = git(["rev-parse", args.release], capture=True).stdout.strip()
        if existing != head:
            fail(f"tag {tag} exists but does not point at {args.release} HEAD")
        log(f"{tag} already exists at {args.release} HEAD; release already cut")
        if args.push:
            push_release(args.release, tag)
        return 0

    temp_root = Path(tempfile.mkdtemp(prefix=f"gwz-py-{tag}-"))
    worktree = temp_root / "gwz-py"
    core_checkout = temp_root / "gwz-core"
    git(["worktree", "add", worktree, args.release])
    try:
        do_merge(worktree, args.main, args.release)
        merged = merge_head_exists(worktree)
        changed = reconcile_cargo_toml(worktree, tag, version)
        checkout_gwz_core(core_url, tag, core_checkout)
        if merged or changed:
            run_release_checks(worktree, args, tag=tag, version=version)
            git_wt(worktree, ["add", "-A"])
            message = f"chore(release): gwz-py {version} (pins gwz-core {tag})"
            git_wt(worktree, ["commit", "-m", message])
            sha = git_wt(worktree, ["rev-parse", "HEAD"], capture=True).stdout.strip()
            log(f"{args.release} reconciled -> {sha[:10]} (gwz-py {version}, gwz-core {tag})")
        else:
            run_release_checks(worktree, args, tag=tag, version=version)
            log(f"{args.release} already reconciled for {tag}; no new commit needed")

        target = git_wt(worktree, ["rev-parse", "HEAD"], capture=True).stdout.strip()
        ensure_tag(tag, target)
        if args.push:
            push_release(args.release, tag)
        else:
            log("next step (not done without --push):")
            log(f"  git -C {REPO} push origin {args.release} {tag}")
        return 0
    finally:
        if args.keep_worktree:
            log(f"left worktree at {worktree}")
        else:
            git(["worktree", "remove", "--force", worktree], check=False)
            git(["worktree", "prune"], check=False)
            shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
