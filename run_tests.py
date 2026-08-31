#!/usr/bin/env python3
"""gwz-py test runner.

Run from the repository root with ``python run_tests.py``. Cross-driver tests
use ``GWZ_RUST_BIN`` when supplied; otherwise the runner builds the adjacent
``gwz-cli`` checkout and exports that exact binary for the test process.
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
    target = Path(env.get("CARGO_TARGET_DIR", "target"))
    if not target.is_absolute():
        target = cli_root / target
    executable = "gwz.exe" if os.name == "nt" else "gwz"
    rust_bin = (target / "debug" / executable).resolve()
    if not rust_bin.is_file() or not os.access(rust_bin, os.X_OK):
        raise RuntimeError(f"cargo build did not produce the gwz CLI at {rust_bin}")
    env["GWZ_RUST_BIN"] = str(rust_bin)
    return rust_bin


def run(cmd: list[str], *, env: dict[str, str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=ROOT, env=env)


def main() -> None:
    env = command_environment()
    rust_bin = provision_rust_cli(env)
    print(f"+ GWZ_RUST_BIN={rust_bin}", flush=True)
    # Never let a stale editable native extension satisfy the Python parity gate.
    run([sys.executable, "-m", "maturin", "develop"], env=env)
    run([sys.executable, "scripts/regen_protocol.py", "--check"], env=env)
    run([sys.executable, "-m", "pytest", "src/tests", "-q"], env=env)


if __name__ == "__main__":
    main()
