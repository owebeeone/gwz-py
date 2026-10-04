"""scripts/build_candidate_extension.py, the committed recipe for gwz-py's
candidate extension, which the transport rows run against.

Its manifests are checked against the gwz-core checkout beside this one, as
the recipe builds them; its build and unpacking against a recorded maturin
command and a wheel made here. Nothing here resolves crates or compiles.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT.parent / "gwz-core"
spec = importlib.util.spec_from_file_location(
    "gwz_py_build_candidate_extension", ROOT / "scripts" / "build_candidate_extension.py"
)
recipe = importlib.util.module_from_spec(spec)
assert spec.loader is not None
# Registered first, as dataclasses look their module up while it executes.
sys.modules[spec.name] = recipe
spec.loader.exec_module(recipe)


def outside_the_workspace(tmp_path: Path) -> Path:
    # pytest's temporary directories are outside every checkout.
    assert not tmp_path.resolve().is_relative_to(ROOT.parent.resolve())
    return tmp_path / "candidate"


@pytest.mark.skipif(not (CORE / "Cargo.toml").is_file(), reason="needs the gwz-core checkout beside gwz-py")
@pytest.mark.skipif(
    os.name == "nt", reason="the candidate route is Unix-only until 1.1.0 S4.5, and its manifests link sources"
)
def test_the_recipe_builds_gwz_py_on_the_core_candidate_manifest(tmp_path: Path) -> None:
    prepared = recipe.prepare(outside_the_workspace(tmp_path), core=CORE)

    core_manifest = (prepared.core / "Cargo.toml").read_text(encoding="utf-8")
    assert "gwz-transport = { path = " in core_manifest, "gwz-core's own prepare.py made it"
    ours = (ROOT / "Cargo.toml").read_text(encoding="utf-8")
    assert prepared.manifest.read_text(encoding="utf-8") == ours.replace(
        'gwz-core = { path = "../gwz-core" }',
        "gwz-core = { path = " + json.dumps(str(prepared.core)) + " }",
    ).replace('path = "../gwz-sspi"', "path = " + json.dumps(str(ROOT.parent / "gwz-sspi")))
    py = prepared.manifest.parent
    assert (py / "Cargo.lock").read_bytes() == (ROOT / "Cargo.lock").read_bytes()
    for name in ("native", "src", "pyproject.toml", "README.md"):
        assert (py / name).is_symlink()
        assert (py / name).resolve() == (ROOT / name).resolve()


def test_the_recipe_refuses_a_destination_that_exists_or_is_inside_the_workspace(
    tmp_path: Path,
) -> None:
    calls: list[object] = []
    with pytest.raises(SystemExit, match="outside the workspace"):
        recipe.prepare(tmp_path, core=CORE, run=calls.append)
    with pytest.raises(SystemExit, match="outside the workspace"):
        recipe.prepare(ROOT.parent / "scratch-candidate", core=CORE, run=calls.append)
    assert calls == []
    assert not (ROOT.parent / "scratch-candidate").exists()


def test_the_recipe_refuses_a_manifest_that_names_no_gwz_core_beside_it(tmp_path: Path) -> None:
    py_root = tmp_path / "workspace" / "gwz-py"
    py_root.mkdir(parents=True)
    (py_root / "Cargo.toml").write_text('[dependencies]\ngwz-core = "=1.1.0"\n', encoding="utf-8")
    destination = outside_the_workspace(tmp_path)
    with pytest.raises(SystemExit, match="gwz-core"):
        recipe.prepare(destination, core=CORE, py_root=py_root, run=lambda *a, **k: None)
    assert not destination.exists(), "it refuses before it makes anything"


@pytest.mark.parametrize("session", [False, True], ids=["transport", "transport-and-session"])
def test_the_recipe_builds_with_the_switches_and_unpacks_the_module(
    tmp_path: Path, session: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RUSTFLAGS", "-C link-arg=-Wl,-dead_strip")
    destination = tmp_path / "candidate"
    (destination / "py").mkdir(parents=True)
    prepared = recipe.Prepared(destination, destination / "core", destination / "py" / "Cargo.toml")
    builds: list[tuple[list[str], dict]] = []

    def maturin(command: list[str], **options: object) -> None:
        builds.append((command, options))
        wheel = Path(command[command.index("--out") + 1]) / "gwz-0.0.0-cp310-abi3-test.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr("gwz/__init__.py", "")
            archive.writestr("gwz/_gwz_core.abi3.so", b"module")
            archive.writestr("gwz/gwz-sspi-worker", b"worker")
            archive.writestr("gwz/sspi-artifact-set.json", b"receipt")

    wheel = recipe.build(
        prepared, python="/venv/python", session=session, target_dir=tmp_path / "target", run=maturin
    )
    [(command, options)] = builds
    # No auditwheel repair: unpack keeps only the gwz/ tree, and a repaired
    # extension needs the gwz.libs/ beside it for the libraries it vendored.
    assert command == [
        "/venv/python", "build_support/sspi_backend.py", "--out", str(prepared.wheels),
        "--build-args", "--profile dev --locked --auditwheel skip",
    ]
    env = options["env"]
    switches = "--cfg gwz_transport_candidate" + (" --cfg gwz_session_candidate" if session else "")
    assert env["RUSTFLAGS"] == "-C link-arg=-Wl,-dead_strip " + switches
    assert env["CARGO_TARGET_DIR"] == str(tmp_path / "target")
    assert options["check"] is True

    module = recipe.unpack(wheel, prepared.extension)
    assert module == prepared.extension / "gwz" / "_gwz_core.abi3.so"
    assert module.read_bytes() == b"module"
    assert (module.parent / "gwz-sspi-worker").read_bytes() == b"worker"
    if os.name != "nt":
        # Windows keeps no executable bit; its worker is the .exe it names.
        assert (module.parent / "gwz-sspi-worker").stat().st_mode & 0o777 == 0o755


def test_the_recipe_prints_the_module_it_built_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    destination = tmp_path / "candidate"
    module = destination / "extension" / "gwz" / "_gwz_core.abi3.so"
    steps: list[str] = []

    def prepare(path: Path, **options: object):
        steps.append("prepare")
        return recipe.Prepared(path, path / "core", path / "py" / "Cargo.toml")

    monkeypatch.setattr(recipe, "prepare", prepare)
    monkeypatch.setattr(recipe, "resolve", lambda prepared, **options: steps.append("resolve"))
    monkeypatch.setattr(recipe, "build", lambda prepared, **options: steps.append("build") or "wheel")
    monkeypatch.setattr(recipe, "unpack", lambda wheel, extension: steps.append("unpack") or module)

    assert recipe.main([str(destination)]) == module
    assert steps == ["prepare", "resolve", "build", "unpack"]
    assert capsys.readouterr().out.splitlines()[-1] == str(module)


def test_resolving_extends_the_copied_lock_with_cargo_metadata(tmp_path: Path) -> None:
    prepared = recipe.Prepared(tmp_path, tmp_path / "core", tmp_path / "py" / "Cargo.toml")
    commands: list[tuple[list[str], dict]] = []
    recipe.resolve(prepared, run=lambda command, **options: commands.append((command, options)))
    assert commands == [
        (
            ["cargo", "metadata", "--format-version", "1", "--manifest-path", str(prepared.manifest)],
            {"check": True, "stdout": subprocess.DEVNULL},
        )
    ]
