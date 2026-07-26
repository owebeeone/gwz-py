from __future__ import annotations

import run_tests


def test_command_environment_identifies_an_unactivated_venv(
    monkeypatch,
    tmp_path,
) -> None:
    venv = tmp_path / "release-python"
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.setattr(run_tests.sys, "prefix", str(venv))
    monkeypatch.setattr(run_tests.sys, "base_prefix", str(tmp_path / "base-python"))

    env = run_tests.command_environment()

    assert env["VIRTUAL_ENV"] == str(venv)


def test_command_environment_preserves_an_activated_venv(
    monkeypatch,
) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", "/already/active")
    monkeypatch.setattr(run_tests.sys, "prefix", "/different/interpreter")
    monkeypatch.setattr(run_tests.sys, "base_prefix", "/base/interpreter")

    env = run_tests.command_environment()

    assert env["VIRTUAL_ENV"] == "/already/active"
