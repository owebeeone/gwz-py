#!/usr/bin/env python3
"""Build/install smoke test for the packaged gwz distribution."""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import shlex
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
DIST_NAME = "gwz"
WHEEL_PREFIX = "gwz"
CONSOLE_SCRIPT = "gwz-py"


def main() -> int:
    args = parse_args()
    smoke_root = Path(tempfile.mkdtemp(prefix="gwz-py-package-smoke."))
    wheel_dir = args.wheel_dir or smoke_root / "wheelhouse"
    try:
        run([sys.executable, "scripts/check_protocol_drift.py"], cwd=ROOT)
        wheel = args.wheel or build_wheel(wheel_dir, args.auditwheel)
        verify_wheel_version(wheel, args.expected_version)
        python, cli = install_wheel(smoke_root, wheel)
        smoke_installed_version(python, args.expected_version)
        smoke_console_script(cli)
        smoke_clone(cli, smoke_root)
    except Exception:
        print(f"package_smoke: failed; preserving {smoke_root}", file=sys.stderr)
        raise
    if args.keep_temp:
        print(f"package_smoke: kept {smoke_root}")
    else:
        remove_tree(smoke_root)
    print("package_smoke: ok")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wheel",
        type=Path,
        help="Existing gwz wheel to install instead of building one.",
    )
    parser.add_argument(
        "--wheel-dir",
        type=Path,
        help="Directory for built wheels. Defaults to a temporary wheelhouse.",
    )
    parser.add_argument(
        "--auditwheel",
        default=None,
        choices=("repair", "check", "skip"),
        help=(
            "maturin auditwheel mode for the release build. Defaults to repair "
            "on macOS/Linux and omitted on Windows."
        ),
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Keep the temporary smoke workspace after a successful run.",
    )
    parser.add_argument(
        "--expected-version",
        help="Assert that the built wheel and installed package use this version.",
    )
    return parser.parse_args()


def build_wheel(wheel_dir: Path, auditwheel: str | None) -> Path:
    wheel_dir.mkdir(parents=True, exist_ok=True)
    auditwheel = auditwheel or default_auditwheel()
    cmd = [
        sys.executable,
        "-m",
        "maturin",
        "build",
        "--release",
        "-o",
        str(wheel_dir),
    ]
    if auditwheel is not None:
        cmd.insert(5, f"--auditwheel={auditwheel}")
    run(cmd, cwd=ROOT)
    wheels = sorted(wheel_dir.glob(f"{WHEEL_PREFIX}-*.whl"), key=lambda path: path.stat().st_mtime)
    if not wheels:
        raise RuntimeError(f"maturin produced no {DIST_NAME} wheel in {wheel_dir}")
    return wheels[-1]


def default_auditwheel() -> str | None:
    return None if platform.system() == "Windows" else "repair"


def remove_tree(path: Path) -> None:
    def allow_write_and_retry(
        func: Callable[[str], object],
        target: str,
        _exc_info: object,
    ) -> None:
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onerror=allow_write_and_retry)


def verify_wheel_version(wheel: Path, expected_version: str | None) -> None:
    if expected_version is None:
        return
    require(
        wheel.name.startswith(f"{WHEEL_PREFIX}-{expected_version}-"),
        f"wheel {wheel.name!r} does not use expected version {expected_version!r}",
    )


def install_wheel(smoke_root: Path, wheel: Path) -> tuple[Path, Path]:
    venv = smoke_root / "venv"
    run([sys.executable, "-m", "venv", str(venv)])
    python = venv_executable(venv, "python")
    run([str(python), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(python), "-m", "pip", "install", str(wheel)])
    return python, venv_executable(venv, CONSOLE_SCRIPT)


def smoke_installed_version(python: Path, expected_version: str | None) -> None:
    if expected_version is None:
        return
    installed = run(
        [
            str(python),
            "-c",
            (
                "from importlib.metadata import version; "
                f"print(version('{DIST_NAME}'))"
            ),
        ],
        capture=True,
    ).stdout.strip()
    require(
        installed == expected_version,
        f"installed {DIST_NAME} version {installed!r}; expected {expected_version!r}",
    )


def smoke_console_script(cli: Path) -> None:
    help_text = run([str(cli), "--help"], capture=True).stdout
    require("clone" in help_text, "gwz-py --help did not advertise clone")


def smoke_clone(cli: Path, smoke_root: Path) -> None:
    source = smoke_root / "source"
    target = smoke_root / "clone"
    member_remote = smoke_root / "member.git"
    source.mkdir()

    run([str(cli), "--root", str(source), "init"])
    run([str(cli), "--root", str(source), "repo", "create", "repos/app"])
    git(source, "config", "user.name", "GWZ Smoke")
    git(source, "config", "user.email", "gwz-smoke@example.invalid")

    member = source / "repos" / "app"
    git(member, "config", "user.name", "GWZ Smoke")
    git(member, "config", "user.email", "gwz-smoke@example.invalid")
    (member / "README.md").write_text("one\n", encoding="utf-8")
    git(member, "add", "README.md")
    git(member, "commit", "-m", "initial")
    member_commit = git(member, "rev-parse", "HEAD", capture=True).stdout.strip()

    run(["git", "init", "--bare", str(member_remote)])
    git(member, "remote", "add", "origin", member_remote.as_uri())
    git(member, "push", "origin", "HEAD:refs/heads/main")
    run([str(cli), "--root", str(source), "repo", "sync", "repos/app"])
    run([str(cli), "--root", str(source), "capture"])

    git(source, "add", "gwz.conf")
    git(source, "commit", "-m", "workspace")

    clone = run([str(cli), "clone", source.as_uri(), str(target)], capture=True)
    status = run([str(cli), "--root", str(target), "status"], capture=True)
    require(clone.stdout.strip() == "ok", f"unexpected clone stdout: {clone.stdout!r}")
    require(status.stdout.strip() == "ok", f"unexpected status stdout: {status.stdout!r}")
    require((target / "gwz.conf" / "gwz.lock.yml").is_file(), "clone did not include gwz lock")
    require(
        (target / "repos" / "app" / "README.md").is_file(),
        "clone did not materialize repos/app",
    )
    cloned_commit = git(target / "repos" / "app", "rev-parse", "HEAD", capture=True).stdout.strip()
    require(cloned_commit == member_commit, "cloned member HEAD does not match source member HEAD")
    require(f"{target}: started" in clone.stderr, "clone did not stream root start")
    require(f"{target}: finished" in clone.stderr, "clone did not stream root finish")
    require("repos/app: started" in clone.stderr, "clone did not stream member start")
    require("repos/app: finished" in clone.stderr, "clone did not stream member finish")


def git(repo: Path, *args: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    return run(["git", "-C", str(repo), *args], capture=capture)


def venv_executable(venv: Path, name: str) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    print("+", shlex.join(cmd), flush=True)
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            print(exc.stdout, file=sys.stderr)
        if exc.stderr:
            print(exc.stderr, file=sys.stderr)
        raise


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


if __name__ == "__main__":
    raise SystemExit(main())
