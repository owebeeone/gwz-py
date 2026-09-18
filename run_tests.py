#!/usr/bin/env python3
"""gwz-py test runner.

Run from the repository root with ``python run_tests.py``. Cross-driver tests
use ``GWZ_RUST_BIN`` when supplied; otherwise the runner builds the adjacent
``gwz-cli`` checkout and exports that exact binary for the test process.

Where that binary lands depends on the checkout: a ``gwz-cli`` that is a member
of the cargo workspace above it is built into the *workspace* ``target/``, and a
standalone ``gwz-cli`` into its own. The runner looks in that order and prints
the binary it chose with the reason, so a stale copy in the other directory is
never picked up silently.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent


def command_environment() -> dict[str, str]:
    env = dict(os.environ)
    # `python /path/to/run_tests.py` does not imply that its virtualenv was
    # activated. Maturin requires VIRTUAL_ENV (or a conventional `.venv`
    # ancestor) for `develop`, so identify the interpreter's environment
    # explicitly when Python is already running from a venv.
    if sys.prefix != sys.base_prefix:
        env.setdefault("VIRTUAL_ENV", sys.prefix)
    local_taut = ROOT.parent / "taut" / "src"
    paths = [str(ROOT / "src")]
    if local_taut.exists():
        paths.append(str(local_taut))
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env.setdefault("SETUPTOOLS_SCM_PRETEND_VERSION", "0.0.0")
    env.setdefault("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_TAUT_PROTO", "0.0.0")
    return env


def is_cargo_workspace_member(cli_root: Path) -> bool:
    """True when the parent directory's Cargo.toml lists `cli_root` as a member.

    In a GWZ workspace, `gwz-cli` is a member of the root cargo workspace, so
    `cargo build` run inside `gwz-cli` writes to the *workspace* target
    directory. Any `gwz-cli/target/debug/gwz` there is a leftover standalone
    build that nothing refreshes; preferring it silently tests a stale binary
    (GwzLaneIssues L5). A standalone `gwz-cli` checkout has no such parent
    manifest and keeps its own `target/`.

    The parent manifest is read as text rather than parsed: the runner takes no
    dependency, and `[workspace] members` is a flat list of path patterns. A
    name that appears anywhere in the members list is enough -- this decides
    which of two directories to look in first, and the answer is checked
    against the filesystem either way.
    """

    parent_manifest = cli_root.parent / "Cargo.toml"
    if not parent_manifest.is_file():
        return False
    try:
        text = parent_manifest.read_text(encoding="utf-8")
    except OSError:
        return False
    if "[workspace]" not in text:
        return False
    name = cli_root.name
    return any(
        quoted in text
        for quoted in (f'"{name}"', f"'{name}'", f'"./{name}"', f"'./{name}'")
    )


def candidate_rust_bins(cli_root: Path, env: dict[str, str]) -> list[tuple[Path, str]]:
    """Where a freshly built `gwz` may be, best first, each with its reason.

    `CARGO_TARGET_DIR` overrides both: cargo honours it from either checkout,
    so there is nothing to choose between.
    """

    executable = "gwz.exe" if os.name == "nt" else "gwz"
    configured_target = env.get("CARGO_TARGET_DIR")
    if configured_target is not None:
        target = Path(configured_target)
        if not target.is_absolute():
            target = cli_root / target
        return [((target / "debug" / executable).resolve(), "CARGO_TARGET_DIR is set")]

    standalone = (cli_root / "target" / "debug" / executable).resolve()
    workspace = (cli_root.parent / "target" / "debug" / executable).resolve()
    if is_cargo_workspace_member(cli_root):
        # D6: cargo builds a workspace member into the workspace target, so the
        # member's own `target/` can only hold a stale standalone build.
        return [
            (workspace, f"{cli_root.name} is a member of the cargo workspace above it"),
            (standalone, "the workspace target holds no gwz"),
        ]
    return [
        (standalone, f"{cli_root.name} is a standalone cargo checkout"),
        (workspace, "the standalone target holds no gwz"),
    ]


def provision_rust_cli(
    env: dict[str, str],
    *,
    root: Path = ROOT,
    run_command: Callable[..., object] = subprocess.run,
) -> Path:
    configured = env.get("GWZ_RUST_BIN")
    if configured:
        rust_bin = Path(configured).expanduser().resolve()
        if not rust_bin.is_file() or not os.access(rust_bin, os.X_OK):
            raise RuntimeError(
                f"GWZ_RUST_BIN does not name an executable file: {rust_bin}"
            )
        env["GWZ_RUST_BIN"] = str(rust_bin)
        print(f"+ gwz CLI: {rust_bin} (GWZ_RUST_BIN names it)", flush=True)
        return rust_bin

    cli_root = root.parent / "gwz-cli"
    if not (cli_root / "Cargo.toml").is_file():
        raise RuntimeError(
            "GWZ_RUST_BIN is unset and no sibling gwz-cli checkout is available"
        )

    run_command(
        ["cargo", "build", "--locked", "--bin", "gwz"],
        check=True,
        cwd=cli_root,
        env=env,
    )
    candidates = candidate_rust_bins(cli_root, env)
    chosen: tuple[Path, str] | None = None
    for candidate, reason in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            chosen = (candidate, reason)
            break
    if chosen is None:
        searched = ", ".join(str(candidate) for candidate, _ in candidates)
        raise RuntimeError(f"cargo build did not produce the gwz CLI at {searched}")
    rust_bin, reason = chosen
    env["GWZ_RUST_BIN"] = str(rust_bin)
    print(f"+ gwz CLI: {rust_bin} ({reason})", flush=True)
    return rust_bin


def run(cmd: list[str], *, env: dict[str, str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT, env=env)


def main() -> None:
    env = command_environment()
    # `provision_rust_cli` prints the binary it chose and why.
    provision_rust_cli(env)
    # Never let a stale editable native extension satisfy the Python parity gate.
    run([sys.executable, "-m", "maturin", "develop"], env=env)
    run([sys.executable, "scripts/regen_protocol.py", "--check"], env=env)
    run([sys.executable, "-m", "pytest", "src/tests", "-q"], env=env)


if __name__ == "__main__":
    main()
