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


def test_candidate_builds_with_the_committed_recipe_and_names_the_module(tmp_path) -> None:
    destination = tmp_path / "candidate"
    module = destination / "extension" / "gwz" / "_gwz_core.abi3.so"
    calls = []

    def recipe(command, **options):
        calls.append((command, options))
        module.parent.mkdir(parents=True)
        module.write_bytes(b"")
        return run_tests.subprocess.CompletedProcess(command, 0, stdout=f"Finished\n{module}\n")

    env = {"PATH": "/bin"}
    assert run_tests.candidate_extension(destination, env, run_command=recipe) == module
    [(command, options)] = calls
    assert command == [
        run_tests.sys.executable,
        str(run_tests.ROOT / "scripts" / "build_candidate_extension.py"),
        str(destination),
    ]
    assert options["check"] is True
    assert env["GWZ_PY_NATIVE_MODULE"] == str(module)


def test_the_candidate_option_runs_the_suite_with_the_built_module(monkeypatch, tmp_path) -> None:
    commands = []
    monkeypatch.setattr(run_tests, "provision_rust_cli", lambda env: None)
    monkeypatch.setattr(run_tests, "run", lambda command, *, env: commands.append((command, dict(env))))

    def candidate_extension(destination, env, **options):
        env["GWZ_PY_NATIVE_MODULE"] = str(destination / "module.so")
        return destination / "module.so"

    monkeypatch.setattr(run_tests, "candidate_extension", candidate_extension)
    monkeypatch.delenv("GWZ_PY_NATIVE_MODULE", raising=False)

    run_tests.main(["--candidate", str(tmp_path / "candidate")])
    command, env = commands[-1]
    assert command[1:4] == ["-m", "pytest", "src/tests"]
    assert env["GWZ_PY_NATIVE_MODULE"] == str(tmp_path / "candidate" / "module.so")

    commands.clear()
    run_tests.main([])
    command, env = commands[-1]
    assert command[1:4] == ["-m", "pytest", "src/tests"]
    assert "GWZ_PY_NATIVE_MODULE" not in env, "without the option the transport rows skip"
