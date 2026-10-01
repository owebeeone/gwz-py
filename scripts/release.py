#!/usr/bin/env python3
"""Reconcile the gwz-py ``release`` branch for a new release.

gwz-py ships from a ``release`` branch that differs from ``main`` in the same
way as gwz-cli: on ``main`` the native crate depends on a sibling
``../gwz-core`` checkout, while on ``release`` it depends on exactly the
gwz-core release of its own version from crates.io, ``gwz-core = "=X.Y.Z"``
(TR3.4 of gwz-core dev-docs/GwzTransportReleasePlanAmendment-2.md, which
replaces the git + tag pin of 1.0.17 and earlier, GwzCratesIoPlan.md D7).

For a release tag ``vX.Y.Z`` this script:

  1. Verifies the matching gwz-core and gwz-cli tags exist: the protocol
     checks read gwz-core at its tag, and the CLI tag supplies the cross-driver
     release fixtures and binary.
  2. Creates a temporary worktree on ``release`` and merges ``main`` into it.
     A conflict in Cargo.toml or Cargo.lock takes main's file: step 3 rewrites
     the only lines in which ``release`` differs, and cargo refreshes the lock.
  3. Sets the gwz-py Cargo package version to ``X.Y.Z`` and pins
     ``gwz-core = "=X.Y.Z"``, migrating the git + tag pin once.
  4. Resolves Cargo.lock from crates.io, where gwz-core X.Y.Z must already be
     published, checks that every native dependency comes from there, checks
     protocol drift against gwz-core at the same tag, runs cargo/Python and
     Rust/Python cross-driver tests, builds an installable wheel, and
     smoke-tests the installed ``gwz-py`` command.
  5. Commits the reconciled release branch and creates the lightweight
     ``vX.Y.Z`` tag without ever moving an existing tag.

The release branch advances only after all checks pass. Pushing is explicit via
``--push``. For the first release, pass ``--bootstrap-release`` to create the
``release`` branch from ``main`` and pin its ``gwz-core`` dependency before
cutting the tag.
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
DEFAULT_GWZ_CLI_URL = "https://github.com/owebeeone/gwz-cli"
RELEASE_PYTHON_DEPS = (
    "maturin>=1.13,<2",
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "setuptools-scm>=8",
    "taut-proto==0.10.0",
)
CRATES_IO_SOURCE = "registry+https://github.com/rust-lang/crates.io-index"
# The gwz-core lines a release may start from, each matched against the whole line: main's
# sibling path (bootstrap, or a Cargo.toml merge conflict resolved toward main), the git + tag
# pin of 1.0.17 and earlier (migrated once), and the registry pin this script writes.
CORE_PINS = (
    re.compile(r'gwz-core\s*=\s*\{\s*path\s*=\s*"\.\./gwz-core"\s*\}'),
    re.compile(r'gwz-core\s*=\s*\{\s*git\s*=\s*"[^"]+"\s*,\s*tag\s*=\s*"[^"]+"\s*\}'),
    re.compile(r'gwz-core\s*=\s*"=[^"\s]+"'),
)
DEPENDENCY_TABLE = re.compile(r"\[(?:target\..+\.)?(?:dev-|build-)?dependencies\]")
LOCK_FIELD = re.compile(r'([A-Za-z0-9_-]+)\s*=\s*"([^"]*)"')


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


def venv_executable(venv: Path, name: str) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name


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


def verify_remote_tag(project: str, url: str, tag: str) -> None:
    result = run(["git", "ls-remote", "--tags", url, f"refs/tags/{tag}"], capture=True)
    if not result.stdout.strip():
        fail(f"{project} tag {tag} not found at {url}; release {project} first")
    log(f"verified {project} {tag} exists at {url}")


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
        other = [path for path in conflicts if path not in ("Cargo.toml", "Cargo.lock")]
        if other:
            git_wt(worktree, ["merge", "--abort"], check=False)
            fail("merge conflicts beyond Cargo.toml and Cargo.lock:\n  " + "\n  ".join(other))
        # release differs from main in Cargo.toml only by the version and the gwz-core pin,
        # which reconcile_cargo_toml rewrites, and in Cargo.lock, which cargo then refreshes.
        for path in conflicts:
            git_wt(worktree, ["checkout", "--theirs", "--", path], check=False)
            git_wt(worktree, ["add", path])
        log(f"resolved merge conflicts in {', '.join(conflicts)} toward {main}")
    elif not merge_head_exists(worktree):
        if already:
            log(f"{release} already contains {main}; reconciling release metadata only")
        else:
            git_wt(worktree, ["merge", "--abort"], check=False)
            fail(f"`git merge {main}` did not produce a merge:\n{merge.stdout}{merge.stderr}")


def manifest_entries(text: str) -> list[tuple[int, str, str]]:
    """Each key line of a Cargo.toml: its index, its table's header and the stripped line."""
    entries, table = [], ""
    for index, line in enumerate(text.split("\n")):
        stripped = line.strip()
        if stripped.startswith("["):
            table = stripped
        elif stripped and not stripped.startswith("#"):
            entries.append((index, table, stripped))
    return entries


def entry_key(line: str) -> str:
    return line.split("=", 1)[0].strip()


def reconcile_cargo_toml(worktree: Path, version: str) -> bool:
    """Pin gwz-core to exactly `version` from crates.io, and set the package version to it.

    Accepts the gwz-core lines in CORE_PINS and refuses every other shape, a `branch` pin, extra
    keys such as `features` or a requirement that is not exact, since rewriting one would
    silently change what the release builds. Returns whether it changed Cargo.toml.
    """
    path = worktree / "Cargo.toml"
    text = path.read_text(encoding="utf-8")
    entries = manifest_entries(text)
    cores = [
        (index, table, line)
        for index, table, line in entries
        if DEPENDENCY_TABLE.fullmatch(table) and entry_key(line) == "gwz-core"
    ]
    if (
        len(cores) != 1
        or cores[0][1] != "[dependencies]"
        or not any(pin.fullmatch(cores[0][2]) for pin in CORE_PINS)
    ):
        found = ", ".join(f"`{line}` in {table}" for _, table, line in cores) or "none"
        fail(
            "Cargo.toml must declare gwz-core on one line of [dependencies], as main's sibling "
            '`path`, the git + tag pin of 1.0.17 and earlier or `gwz-core = "=X.Y.Z"` from '
            f"crates.io (found: {found}) -- did main edit the dependency line?"
        )
    versions = [
        index
        for index, table, line in entries
        if table == "[package]" and entry_key(line) == "version"
    ]
    if len(versions) != 1:
        fail(f"expected one `version` line in Cargo.toml's [package] table, found {len(versions)}")
    lines = text.split("\n")
    lines[cores[0][0]] = f'gwz-core = "={version}"'
    lines[versions[0]] = re.sub(r'"[^"]*"', f'"{version}"', lines[versions[0]], count=1)
    updated = "\n".join(lines)
    if updated == text:
        log(f'Cargo.toml already reconciled (version = {version}, gwz-core = "={version}")')
        return False
    path.write_text(updated, encoding="utf-8", newline="\n")
    log(f'reconciled Cargo.toml: version = {version}, gwz-core = "={version}" from crates.io')
    return True


def verify_pyproject_metadata(worktree: Path) -> None:
    pyproject = (worktree / "pyproject.toml").read_text(encoding="utf-8")
    if not re.search(r'^name = "gwz"$', pyproject, re.M):
        fail("pyproject.toml must publish the Python distribution as `gwz`")
    if 'gwz-py = "gwz.cli:main"' not in pyproject:
        fail("pyproject.toml must install the Python CLI as `gwz-py`")


def lock_packages(lock: str) -> list[dict[str, str]]:
    """The `[[package]]` entries of a Cargo.lock, each as its quoted string fields."""
    packages: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lock.splitlines():
        stripped = line.strip()
        if stripped == "[[package]]":
            current = {}
            packages.append(current)
        elif stripped.startswith("["):
            current = None
        elif current is not None and (field := LOCK_FIELD.fullmatch(stripped)):
            current[field.group(1)] = field.group(2)
    return packages


def verify_release_pins(worktree: Path, version: str) -> None:
    """Refuse a release whose native dependencies do not all come from crates.io.

    Cargo.toml must pin `gwz-core = "=X.Y.Z"`, gwz-py's own version, and declare no `git` or
    `path` dependency; Cargo.lock must take gwz-core X.Y.Z with its checksum, and every package
    but gwz-py itself, from crates.io. The publish workflow runs this check too. Each native
    dependency pin is logged with what the lock resolved for it.
    """
    pin = f'gwz-core = "={version}"'
    manifest = (worktree / "Cargo.toml").read_text(encoding="utf-8")
    dependencies = [
        (table, line)
        for _, table, line in manifest_entries(manifest)
        if DEPENDENCY_TABLE.fullmatch(table)
    ]
    cores = [line for _, line in dependencies if entry_key(line) == "gwz-core"]
    if cores != [pin] or ("[dependencies]", pin) not in dependencies:
        tagged = any(re.search(r"\btag\s*=", line) for line in cores)
        fail(
            f"Cargo.toml must pin `{pin}` from crates.io, gwz-py's own version, in "
            f"[dependencies]; found {cores or 'no gwz-core'}"
            + (" -- the git tag pin of 1.0.17 and earlier is not a release form" if tagged else "")
        )
    sourced = [line for _, line in dependencies if re.search(r"\b(?:git|path)\s*=", line)]
    if sourced:
        fail("Cargo.toml declares a git or path dependency: " + "; ".join(sourced))
    packages = lock_packages((worktree / "Cargo.lock").read_text(encoding="utf-8"))
    core = [package for package in packages if package.get("name") == "gwz-core"]
    if [(entry.get("version"), entry.get("source")) for entry in core] != [
        (version, CRATES_IO_SOURCE)
    ]:
        fail(f"Cargo.lock must take gwz-core {version} from crates.io; it holds {core}")
    if not core[0].get("checksum"):
        fail(f"Cargo.lock has no checksum for gwz-core {version}")
    stray = [
        f"{package.get('name')} {package.get('version')} ({package.get('source', 'a path')})"
        for package in packages
        if package.get("name") != "gwz-py" and package.get("source") != CRATES_IO_SOURCE
    ]
    if stray:
        fail("Cargo.lock takes packages from somewhere other than crates.io: " + "; ".join(stray))
    for _, line in dependencies:
        renamed = re.search(r'\bpackage\s*=\s*"([^"]+)"', line)
        name = renamed.group(1) if renamed else entry_key(line)
        locked = [entry.get("version", "?") for entry in packages if entry.get("name") == name]
        if not locked:
            fail(f"Cargo.lock has no {name} for Cargo.toml's `{line}`; refresh the lock")
        log(f"native dependency pin: {line} -> {name} {', '.join(locked)} from crates.io")


def resolve_release_lock(worktree: Path, version: str) -> None:
    """Resolve Cargo.lock for the reconciled manifest. `--workspace` re-resolves only what the
    manifest changed and leaves every other locked version alone, also when the merge took
    main's lock, whose gwz-core is the sibling path."""
    if run(["cargo", "update", "--workspace"], cwd=worktree, check=False).returncode != 0:
        fail(
            f"cargo could not resolve the release pins: gwz-core {version} must be on crates.io "
            "before gwz-py's release (release gwz-core first and let its publish job finish)"
        )


def checkout_release_dependency(url: str, tag: str, target: Path) -> None:
    run(["git", "clone", "--depth", "1", "--branch", tag, url, target])


def create_release_python(worktree: Path) -> Path:
    venv = worktree.parent / ".release-python"
    run([sys.executable, "-m", "venv", venv])
    python = venv_executable(venv, "python")
    run([python, "-m", "pip", "install", "--upgrade", "pip"])
    run([python, "-m", "pip", "install", *RELEASE_PYTHON_DEPS])
    return python


def run_release_checks(worktree: Path, args: argparse.Namespace, *, version: str) -> None:
    python = create_release_python(worktree)
    verify_pyproject_metadata(worktree)
    run([python, "scripts/check_protocol_drift.py"], cwd=worktree)
    run([python, "scripts/regen_protocol.py", "--check"], cwd=worktree)
    run(["cargo", "check"], cwd=worktree)
    verify_release_pins(worktree, version)
    if not args.no_test:
        run([python, "run_tests.py"], cwd=worktree)
    if not args.no_package_smoke:
        run(
            [
                python,
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

    verify_remote_tag("gwz-core", args.gwz_core_url, tag)
    verify_remote_tag("gwz-cli", args.gwz_cli_url, tag)

    temp_root = Path(tempfile.mkdtemp(prefix=f"gwz-py-{tag}-bootstrap-"))
    worktree = temp_root / "gwz-py"
    core_checkout = temp_root / "gwz-core"
    cli_checkout = temp_root / "gwz-cli"
    committed = False

    git(["branch", args.release, args.main])
    try:
        release_branch_is_free(args.release)
        git(["worktree", "add", worktree, args.release])
        reconcile_cargo_toml(worktree, version)
        checkout_release_dependency(args.gwz_core_url, tag, core_checkout)
        checkout_release_dependency(args.gwz_cli_url, tag, cli_checkout)
        resolve_release_lock(worktree, version)
        run_release_checks(worktree, args, version=version)
        git_wt(worktree, ["add", "Cargo.toml", "Cargo.lock"])
        staged = git_wt(
            worktree,
            ["diff", "--cached", "--name-only"],
            capture=True,
        ).stdout.split()
        if not staged:
            fail("bootstrap produced no release metadata changes")
        message = (
            f"chore(release): initialize gwz-py {version} "
            f"(pins gwz-core {version} from crates.io)"
        )
        git_wt(worktree, ["commit", "-m", message])
        committed = True
        target = git_wt(worktree, ["rev-parse", "HEAD"], capture=True).stdout.strip()
        log(f"{args.release} initialized -> {target[:10]} (gwz-py {version}, gwz-core {version})")
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
        help="gwz-core git URL whose matching tag is verified and checked out for the checks",
    )
    parser.add_argument(
        "--gwz-cli-url",
        default=DEFAULT_GWZ_CLI_URL,
        help="gwz-cli git URL used for matching cross-driver release tests",
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

    verify_remote_tag("gwz-core", args.gwz_core_url, tag)
    verify_remote_tag("gwz-cli", args.gwz_cli_url, tag)

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
    cli_checkout = temp_root / "gwz-cli"
    git(["worktree", "add", worktree, args.release])
    try:
        do_merge(worktree, args.main, args.release)
        merged = merge_head_exists(worktree)
        changed = reconcile_cargo_toml(worktree, version)
        checkout_release_dependency(args.gwz_core_url, tag, core_checkout)
        checkout_release_dependency(args.gwz_cli_url, tag, cli_checkout)
        resolve_release_lock(worktree, version)
        changed = changed or bool(git_wt(worktree, ["status", "--porcelain"], capture=True).stdout)
        if merged or changed:
            run_release_checks(worktree, args, version=version)
            git_wt(worktree, ["add", "-A"])
            message = f"chore(release): gwz-py {version} (pins gwz-core {version} from crates.io)"
            git_wt(worktree, ["commit", "-m", message])
            sha = git_wt(worktree, ["rev-parse", "HEAD"], capture=True).stdout.strip()
            log(f"{args.release} reconciled -> {sha[:10]} (gwz-py {version}, gwz-core {version})")
        else:
            run_release_checks(worktree, args, version=version)
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
