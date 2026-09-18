from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import run_tests


def _binary_name() -> str:
    return "gwz.exe" if os.name == "nt" else "gwz"


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


def _checkouts(tmp_path: Path, *, workspace_member: bool) -> tuple[Path, Path]:
    """A `gwz-py`/`gwz-cli` pair, with or without a cargo workspace above them."""

    py_root = tmp_path / "gwz-py"
    cli_root = tmp_path / "gwz-cli"
    py_root.mkdir()
    cli_root.mkdir()
    (cli_root / "Cargo.toml").write_text("[package]\nname='gwz'\n", encoding="utf-8")
    if workspace_member:
        (tmp_path / "Cargo.toml").write_text(
            '[workspace]\nresolver = "2"\nmembers = ["gwz-cli", "gwz-core"]\n',
            encoding="utf-8",
        )
    return py_root, cli_root


def test_a_standalone_sibling_cli_is_not_a_workspace_member(tmp_path: Path) -> None:
    _, cli_root = _checkouts(tmp_path, workspace_member=False)

    assert not run_tests.is_cargo_workspace_member(cli_root)


def test_a_sibling_cli_listed_by_the_parent_manifest_is_a_workspace_member(
    tmp_path: Path,
) -> None:
    _, cli_root = _checkouts(tmp_path, workspace_member=True)

    assert run_tests.is_cargo_workspace_member(cli_root)


def test_a_parent_manifest_that_is_not_a_workspace_does_not_make_a_member(
    tmp_path: Path,
) -> None:
    # A plain parent package that happens to name the directory (a path
    # dependency, say) is not a workspace: cargo still builds into `gwz-cli`'s
    # own target.
    _, cli_root = _checkouts(tmp_path, workspace_member=False)
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "outer"\n\n[dependencies]\ngwz = { path = "gwz-cli" }\n',
        encoding="utf-8",
    )

    assert not run_tests.is_cargo_workspace_member(cli_root)


def test_a_workspace_member_cli_prefers_the_workspace_target(tmp_path: Path) -> None:
    # D6: cargo builds a member into the workspace target, so a binary left in
    # `gwz-cli/target` is a stale standalone build that nothing refreshes.
    _, cli_root = _checkouts(tmp_path, workspace_member=True)
    env: dict[str, str] = {}

    candidates = run_tests.candidate_rust_bins(cli_root, env)

    assert [candidate for candidate, _ in candidates] == [
        (tmp_path / "target" / "debug" / _binary_name()).resolve(),
        (cli_root / "target" / "debug" / _binary_name()).resolve(),
    ]


def test_a_standalone_cli_prefers_its_own_target(tmp_path: Path) -> None:
    _, cli_root = _checkouts(tmp_path, workspace_member=False)
    env: dict[str, str] = {}

    candidates = run_tests.candidate_rust_bins(cli_root, env)

    assert [candidate for candidate, _ in candidates] == [
        (cli_root / "target" / "debug" / _binary_name()).resolve(),
        (tmp_path / "target" / "debug" / _binary_name()).resolve(),
    ]


def test_cargo_target_dir_leaves_nothing_to_choose(tmp_path: Path) -> None:
    _, cli_root = _checkouts(tmp_path, workspace_member=True)
    elsewhere = tmp_path / "shared-target"
    env = {"CARGO_TARGET_DIR": str(elsewhere)}

    candidates = run_tests.candidate_rust_bins(cli_root, env)

    assert [candidate for candidate, _ in candidates] == [
        (elsewhere / "debug" / _binary_name()).resolve()
    ]


def test_runner_picks_the_workspace_build_over_a_stale_member_target(
    tmp_path: Path,
) -> None:
    # Both directories hold a binary; only the workspace one was just built.
    py_root, cli_root = _checkouts(tmp_path, workspace_member=True)
    _executable(cli_root / "target" / "debug" / _binary_name())
    env: dict[str, str] = {}

    def fake_run(_command: list[str], **_kwargs: Any) -> None:
        _executable(tmp_path / "target" / "debug" / _binary_name())

    observed = run_tests.provision_rust_cli(env, root=py_root, run_command=fake_run)

    assert observed == (tmp_path / "target" / "debug" / _binary_name()).resolve()
    assert env["GWZ_RUST_BIN"] == str(observed)


def test_runner_builds_and_exports_the_checked_out_sibling_cli(tmp_path: Path) -> None:
    py_root, cli_root = _checkouts(tmp_path, workspace_member=False)
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


def test_runner_finds_a_sibling_cli_built_in_the_parent_workspace(tmp_path: Path) -> None:
    # A standalone checkout whose own target is empty still falls back to the
    # directory above, so an unrecognised layout is not a hard failure.
    py_root, _cli_root = _checkouts(tmp_path, workspace_member=False)
    env: dict[str, str] = {}

    def fake_run(_command: list[str], **_kwargs: Any) -> None:
        _executable(tmp_path / "target" / "debug" / _binary_name())

    observed = run_tests.provision_rust_cli(
        env,
        root=py_root,
        run_command=fake_run,
    )

    assert observed.parent.parent == (tmp_path / "target").resolve()
    assert env["GWZ_RUST_BIN"] == str(observed)
