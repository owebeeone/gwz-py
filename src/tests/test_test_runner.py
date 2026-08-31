from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import run_tests


def _executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test binary")
    path.chmod(0o755)


def test_runner_preserves_an_explicit_exact_rust_cli(tmp_path: Path) -> None:
    rust_bin = tmp_path / "authority" / "gwz"
    _executable(rust_bin)
    env = {"GWZ_RUST_BIN": str(rust_bin)}

    observed = run_tests.provision_rust_cli(env, root=tmp_path / "gwz-py")

    assert observed == rust_bin.resolve()
    assert env["GWZ_RUST_BIN"] == str(rust_bin.resolve())


def test_runner_builds_and_exports_the_checked_out_sibling_cli(tmp_path: Path) -> None:
    py_root = tmp_path / "gwz-py"
    cli_root = tmp_path / "gwz-cli"
    py_root.mkdir()
    cli_root.mkdir()
    (cli_root / "Cargo.toml").write_text("[package]\nname='gwz'\n", encoding="utf-8")
    env: dict[str, str] = {}
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(command: list[str], **kwargs: Any) -> None:
        calls.append((command, kwargs))
        name = "gwz.exe" if os.name == "nt" else "gwz"
        _executable(cli_root / "target" / "debug" / name)

    observed = run_tests.provision_rust_cli(
        env,
        root=py_root,
        run_command=fake_run,
    )

    assert calls == [
        (
            ["cargo", "build", "--locked", "--bin", "gwz"],
            {"check": True, "cwd": cli_root, "env": env},
        )
    ]
    assert observed.is_file()
    assert env["GWZ_RUST_BIN"] == str(observed)
