"""gwz-py's release pins (TR3.4 of gwz-core dev-docs/GwzTransportReleasePlanAmendment-2.md).

From 1.1.0 the `release` branch pins `gwz-core = "=X.Y.Z"` from crates.io, gwz-py's own version,
in place of the git tag it pinned through 1.0.17 (gwz-core dev-docs/GwzCratesIoPlan.md D7), and
no native dependency comes from git or a path. Checked here without a network or cargo:

- `scripts/release.py` writes the registry form from each shape a release starts from, and
  refuses any other;
- its pin check, which the publish workflow runs too, accepts the registry release and refuses
  a git-tag pin, a version skew, and any package that does not come from crates.io;
- 1.1.0's merge of main into release, whose Cargo.toml conflicts, ends on the registry pin;
- the publish workflow's own step refuses a git-tag pin and accepts `=1.1.0`;
- RELEASE.md names every native dependency pin.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10, where pytest brings tomli
    import tomli as tomllib


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("gwz_py_release", ROOT / "scripts" / "release.py")
release = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(release)

CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"
MAIN_PIN = 'gwz-core = { path = "../gwz-core" }'
GIT_TAG_PIN = 'gwz-core = {{ git = "https://github.com/owebeeone/gwz-core", tag = "v{}" }}'
REGISTRY_PIN = 'gwz-core = "={}"'
GIT_CORE_SOURCE = "git+https://github.com/owebeeone/gwz-core?tag=v1.1.0#" + "a" * 40
# Each crate main's Cargo.toml declares, by the name the lock records it under.
DECLARED = {
    name: requirement.get("package", name) if isinstance(requirement, dict) else name
    for name, requirement in tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))[
        "dependencies"
    ].items()
}


def release_manifest(pin: str, version: str) -> str:
    """This repository's Cargo.toml as the release branch holds it at `version`, with `pin`."""
    manifest = (ROOT / "Cargo.toml").read_text(encoding="utf-8")
    assert manifest.count(MAIN_PIN) == 1, f"Cargo.toml on main no longer carries `{MAIN_PIN}`"
    manifest = manifest.replace(MAIN_PIN, pin)
    return re.sub(r'^version = "[^"]*"', f'version = "{version}"', manifest, count=1, flags=re.M)


def lock_entry(name: str, version: str, *, source=CRATES_IO, checksum="5" * 64) -> str:
    lines = ["[[package]]", f'name = "{name}"', f'version = "{version}"']
    if source is not None:
        lines.append(f'source = "{source}"')
    if checksum is not None:
        lines.append(f'checksum = "{checksum}"')
    return "\n".join(lines) + "\n"


def release_lock(replaced: dict[str, str] | None = None) -> str:
    """The 1.1.0 registry release's lock: gwz-py itself, each declared crate and an internal
    crate gwz-core brings in, all but gwz-py from crates.io, with `replaced` entries swapped in."""
    entries = {
        package: lock_entry(package, "1.1.0" if package == "gwz-core" else "1.0.0")
        for package in DECLARED.values()
    }
    entries["gwz-ids"] = lock_entry("gwz-ids", "0.0.9")
    entries["gwz-py"] = lock_entry("gwz-py", "1.1.0", source=None, checksum=None)
    entries.update(replaced or {})
    return "version = 4\n\n" + "\n".join(entries.values())


def release_tree(root: Path, pin: str, lock: str) -> Path:
    (root / "Cargo.toml").write_text(release_manifest(pin, "1.1.0"), encoding="utf-8")
    (root / "Cargo.lock").write_text(lock, encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "pin",
    [MAIN_PIN, GIT_TAG_PIN.format("1.0.17"), REGISTRY_PIN.format("1.0.17")],
    ids=["main-path", "git-tag-of-1.0.17", "registry"],
)
def test_release_script_writes_the_registry_pin_of_the_release_version(
    tmp_path: Path, pin: str
) -> None:
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(release_manifest(pin, "1.0.17"), encoding="utf-8")

    assert release.reconcile_cargo_toml(tmp_path, "1.1.0") is True
    expected = release_manifest(REGISTRY_PIN.format("1.1.0"), "1.1.0")
    assert manifest.read_text(encoding="utf-8") == expected
    assert release.reconcile_cargo_toml(tmp_path, "1.1.0") is False


@pytest.mark.parametrize(
    "pin",
    [
        'gwz-core = { git = "https://github.com/owebeeone/gwz-core", branch = "main" }',
        'gwz-core = { path = "../gwz-core", features = ["candidate"] }',
        'gwz-core = "1.1.0"',
    ],
    ids=["git-branch", "path-with-features", "caret-requirement"],
)
def test_release_script_refuses_any_other_gwz_core_shape(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], pin: str
) -> None:
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text(release_manifest(pin, "1.0.17"), encoding="utf-8")

    with pytest.raises(SystemExit):
        release.reconcile_cargo_toml(tmp_path, "1.1.0")

    assert 'gwz-core = "=X.Y.Z"' in capsys.readouterr().err
    assert manifest.read_text(encoding="utf-8") == release_manifest(pin, "1.0.17")


def test_pin_check_accepts_the_registry_release_and_names_every_native_dependency_pin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    release.verify_release_pins(
        release_tree(tmp_path, REGISTRY_PIN.format("1.1.0"), release_lock()), "1.1.0"
    )

    out = capsys.readouterr().out
    for name in DECLARED:
        assert f"native dependency pin: {name} = " in out
    assert 'gwz-core = "=1.1.0" -> gwz-core 1.1.0 from crates.io' in out


@pytest.mark.parametrize(
    ("pin", "replaced", "reason"),
    [
        pytest.param(
            GIT_TAG_PIN.format("1.1.0"),
            {"gwz-core": lock_entry("gwz-core", "1.1.0", source=GIT_CORE_SOURCE, checksum=None)},
            "git tag",
            id="git-tag-pin",
        ),
        pytest.param(
            REGISTRY_PIN.format("1.0.17"),
            {"gwz-core": lock_entry("gwz-core", "1.0.17")},
            'gwz-core = "=1.1.0"',
            id="version-skew",
        ),
        pytest.param(
            MAIN_PIN,
            {"gwz-core": lock_entry("gwz-core", "1.1.0", source=None, checksum=None)},
            'gwz-core = "=1.1.0"',
            id="main-path",
        ),
        pytest.param(
            REGISTRY_PIN.format("1.1.0"),
            {"gwz-core": lock_entry("gwz-core", "1.1.0", source=GIT_CORE_SOURCE)},
            "gwz-core 1.1.0 from crates.io",
            id="core-from-git-in-the-lock",
        ),
        pytest.param(
            REGISTRY_PIN.format("1.1.0"),
            {"gwz-core": lock_entry("gwz-core", "1.1.0", checksum=None)},
            "checksum",
            id="core-without-a-checksum",
        ),
        pytest.param(
            REGISTRY_PIN.format("1.1.0"),
            {"gwz-ids": lock_entry("gwz-ids", "0.0.9", source=None, checksum=None)},
            "gwz-ids 0.0.9",
            id="internal-crate-from-a-path",
        ),
        pytest.param(
            REGISTRY_PIN.format("1.1.0"),
            {"pyo3": lock_entry("pyo3", "0.28.3", source="git+https://github.com/PyO3/pyo3#b")},
            "pyo3 0.28.3",
            id="dependency-from-git-in-the-lock",
        ),
    ],
)
def test_pin_check_refuses_a_release_not_wholly_from_crates_io(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    pin: str,
    replaced: dict[str, str],
    reason: str,
) -> None:
    tree = release_tree(tmp_path, pin, release_lock(replaced))

    with pytest.raises(SystemExit):
        release.verify_release_pins(tree, "1.1.0")

    assert reason in capsys.readouterr().err


def test_pin_check_refuses_a_git_or_path_dependency_in_any_dependency_table(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    helper = lock_entry("helper", "0.1.0", source=None, checksum=None)
    tree = release_tree(tmp_path, REGISTRY_PIN.format("1.1.0"), release_lock({"helper": helper}))
    with (tree / "Cargo.toml").open("a", encoding="utf-8") as manifest:
        manifest.write("\n[target.'cfg(windows)'.dependencies]\n")
        manifest.write('helper = { path = "../helper" }\n')

    with pytest.raises(SystemExit):
        release.verify_release_pins(tree, "1.1.0")

    assert 'helper = { path = "../helper" }' in capsys.readouterr().err


def test_a_cargo_toml_merge_conflict_takes_main_and_ends_on_the_registry_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1.1.0's merge: main added `cfg-if` beside the gwz-core line that release pins by git tag,
    so Cargo.toml conflicts, as it does between gwz-py's main and release today."""
    repo = tmp_path / "gwz-py"
    repo.mkdir()
    # An identity for the fixture's commits, and for the script's git calls, on a host with none.
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "release test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "release@example.invalid")

    def git(*args: str) -> None:
        command = ["git", "-c", "commit.gpgsign=false", "-C", str(repo), *args]
        subprocess.run(command, check=True, capture_output=True, text=True)

    def commit(manifest: str, message: str) -> None:
        (repo / "Cargo.toml").write_text(manifest, encoding="utf-8")
        git("add", "Cargo.toml")
        git("commit", "-q", "-m", message)

    base = f'[package]\nname = "gwz-py"\nversion = "0.0.0"\n\n[dependencies]\n{MAIN_PIN}\n'
    base += 'pyo3 = "0.28.3"\n\n[workspace]\n'
    main = base.replace(MAIN_PIN, f'cfg-if = "1"\n{MAIN_PIN}')
    git("init", "-q", "-b", "main")
    commit(base, "base")
    git("checkout", "-q", "-b", "release")
    pinned = base.replace(MAIN_PIN, GIT_TAG_PIN.format("1.0.17"))
    commit(pinned.replace('"0.0.0"', '"1.0.17"'), "release 1.0.17")
    git("checkout", "-q", "main")
    commit(main, "add cfg-if beside gwz-core")
    git("checkout", "-q", "release")
    monkeypatch.setattr(release, "REPO", repo)

    release.do_merge(repo, "main", "release")
    release.reconcile_cargo_toml(repo, "1.1.0")

    expected = main.replace(MAIN_PIN, REGISTRY_PIN.format("1.1.0")).replace('"0.0.0"', '"1.1.0"')
    assert (repo / "Cargo.toml").read_text(encoding="utf-8") == expected


def run_publish_metadata_step(root: Path, pin: str, lock: str) -> subprocess.CompletedProcess[str]:
    """Run publish.yml's "Verify release metadata" step, for tag v1.1.0, over a release tree."""
    lines = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    lines = lines.splitlines()
    step = lines.index("      - name: Verify release metadata")
    run = next(index for index in range(step, len(lines)) if lines[index].strip() == "run: |")
    indent = " " * (len(lines[run]) - len(lines[run].lstrip()) + 2)
    script = []
    for line in lines[run + 1 :]:
        if line.strip() and not line.startswith(indent):
            break
        script.append(line[len(indent) :])
    source = "\n".join(script).replace("${{ needs.resolve.outputs.tag }}", "v1.1.0")
    source = source.replace("${{ needs.resolve.outputs.version }}", "1.1.0")
    assert "${{" not in source
    release_tree(root, pin, lock)
    (root / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "release.py", root / "scripts" / "release.py")
    shutil.copy(ROOT / "pyproject.toml", root / "pyproject.toml")
    (root / "step.py").write_text(source, encoding="utf-8")
    command = [sys.executable, "step.py"]
    return subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)


def test_publish_workflow_refuses_a_git_tag_pin(tmp_path: Path) -> None:
    git_core = lock_entry("gwz-core", "1.1.0", source=GIT_CORE_SOURCE, checksum=None)
    lock = release_lock({"gwz-core": git_core})

    result = run_publish_metadata_step(tmp_path, GIT_TAG_PIN.format("1.1.0"), lock)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "git tag" in result.stderr


def test_publish_workflow_accepts_the_registry_pin_and_names_every_native_dependency_pin(
    tmp_path: Path,
) -> None:
    result = run_publish_metadata_step(tmp_path, REGISTRY_PIN.format("1.1.0"), release_lock())

    assert result.returncode == 0, result.stdout + result.stderr
    for name in DECLARED:
        assert f"native dependency pin: {name} = " in result.stdout


def test_release_md_names_every_native_dependency_pin() -> None:
    text = (ROOT / "RELEASE.md").read_text(encoding="utf-8")
    assert "\n## Native dependency pins\n" in text
    section = text.split("\n## Native dependency pins\n", 1)[1].split("\n## ", 1)[0]

    for name in DECLARED:
        assert f"`{name}`" in section, f"RELEASE.md's native dependency pins do not name `{name}`"
    assert '`gwz-core = "=X.Y.Z"`' in section
