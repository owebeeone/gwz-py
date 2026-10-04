from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CI = REPO_ROOT / ".github" / "workflows" / "package-smoke.yml"
CANDIDATE = REPO_ROOT / ".github" / "workflows" / "transport-candidate.yml"
T, S = "gwz_transport_candidate", "gwz_session_candidate"


def job(workflow: Path, name: str) -> str:
    """The text of the job `name` in `workflow`, from its key to the next job's."""
    lines = workflow.read_text(encoding="utf-8").splitlines()
    assert f"  {name}:" in lines, f"{workflow.name} has no job {name}"
    start = lines.index(f"  {name}:")
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"  [\w-]+:", lines[i])), len(lines))
    return "\n".join(lines[start:end])


def steps(job_text: str) -> list[str]:
    """A job's steps, each from its `- ` to the next one's."""
    found: list[list[str]] = []
    for line in job_text.splitlines():
        if line.startswith("      - "):
            found.append([line])
        elif found:
            found[-1].append(line)
    return ["\n".join(step) for step in found]


def step(job_text: str, text: str) -> str:
    """The one step of a job that holds `text`."""
    matching = [each for each in steps(job_text) if text in each]
    assert len(matching) == 1, f"{len(matching)} steps hold {text!r}"
    return matching[0]


def test_python_test_workflows_activate_a_real_virtualenv() -> None:
    for relative_path in (
        ".github/workflows/package-smoke.yml",
        ".github/workflows/publish.yml",
        ".github/workflows/transport-candidate.yml",
    ):
        workflow = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "Create Python virtual environment" in workflow
        assert 'root = pathlib.Path(".venv").resolve()' in workflow
        assert 'print(f"VIRTUAL_ENV={root}", file=env_file)' in workflow
        assert 'os.environ["GITHUB_PATH"]' in workflow


def test_the_candidate_job_and_the_rust_test_job_exist() -> None:
    """CI runs the transport rows on the candidate extension, and gwz-py's Rust
    unit tests, neither of which the ordinary validate job runs."""
    assert job(CANDIDATE, "candidate")
    assert job(CI, "rust-tests")


def test_the_candidate_job_runs_the_whole_suite_on_the_recipes_extension_on_linux() -> None:
    """`run_tests.py --candidate DIR` builds the extension with the committed
    recipe, scripts/build_candidate_extension.py, and names it to the suite in
    GWZ_PY_NATIVE_MODULE (test_run_tests.py), so the transport rows run rather
    than skip. DIR is under RUNNER_TEMP, outside the workspace, as the recipe
    requires. Nothing narrows the suite or names another module."""
    candidate = job(CANDIDATE, "candidate")
    assert re.findall(r"^    runs-on: *(.+)$", candidate, re.M) == ["ubuntu-24.04"]
    suite = step(candidate, "run: python run_tests.py")
    assert re.findall(r"^        run: *(.+)$", suite, re.M) == [
        'python run_tests.py --candidate "$RUNNER_TEMP/candidate" ${{ matrix.session }}'
    ]
    assert re.findall(r"^        working-directory: *(.+)$", suite, re.M) == ["gwz-py"]
    assert not re.search(r"^ +GWZ_PY_NATIVE_MODULE:", candidate, re.M)
    assert not re.search(r"-m pytest|--deselect|\s-k\s", candidate)


def test_the_candidate_job_checks_out_what_the_recipe_builds_against_beside_gwz_py() -> None:
    """gwz-core at main, as gwz-py's other jobs build against it; git2-rs at the
    commit gwz-core pins; gwz-transport at the commit gwz-core's own candidate
    job builds against; taut at the tag of gwz-py's taut-proto release, as a
    workspace keeps it; gwz-cli, whose CLI run_tests.py builds for the
    cross-driver rows; and gwz-sspi, which gwz-py, gwz-cli and the candidate
    manifest name by path."""
    candidate = job(CANDIDATE, "candidate")
    for repository, path in (
        ("owebeeone/gwz-core", "gwz-core"),
        ("owebeeone/gwz-transport", "gwz-transport"),
        ("owebeeone/taut", "taut"),
        ("owebeeone/gwz-cli", "gwz-cli"),
        ("owebeeone/gwz-sspi", "gwz-sspi"),
    ):
        checkout = step(candidate, f"repository: {repository}\n")
        assert re.findall(r"^          path: *(.+)$", checkout, re.M) == [path]
    git2 = step(candidate, "checkout-git2-rs.sh")
    assert re.findall(r"^        run: *(.+)$", git2, re.M) == ["bash .github/checkout-git2-rs.sh"]
    assert re.findall(r"^        working-directory: *(.+)$", git2, re.M) == ["gwz-core"]
    pins = step(candidate, "id: pins")
    assert "gwz-core/.github/gwz-transport.commit" in pins
    assert "gwz-py/pyproject.toml" in pins
    assert "ref: ${{ steps.pins.outputs.gwz-transport }}" in step(candidate, "repository: owebeeone/gwz-transport\n")
    assert "ref: ${{ steps.pins.outputs.taut }}" in step(candidate, "repository: owebeeone/taut\n")


def test_the_candidate_job_keeps_both_candidate_shapes_green() -> None:
    """Rule (a) of gwz-core dev-docs/GwzTransportReleasePlanAmendment-2.md
    §3.13 (TR2.12): until S7.1 (1.1.0) removes gwz_transport_candidate, CI keeps
    two candidate shapes green, the transport switch alone and both switches,
    one leg each, as gwz-core's transport candidate job does. A leg's
    rustflags are the switches the recipe builds it with."""
    candidate = job(CANDIDATE, "candidate")
    assert re.findall(r"^ +(?:- )?leg: *(.+)$", candidate, re.M) == ["transport", "transport-and-session"]
    assert re.findall(r"^ +(?:- )?session: *(.+)$", candidate, re.M) == ['""', "--session"]
    assert re.findall(r"^ +(?:- )?rustflags: *(.+)$", candidate, re.M) == [f"--cfg {T}", f"--cfg {T} --cfg {S}"]
    assert "fail-fast: false" in candidate


def test_the_candidate_job_runs_the_candidate_builds_rust_unit_tests_after_the_suite() -> None:
    """The recipe leaves the candidate manifest, its resolved lock and its
    build in RUNNER_TEMP/candidate, and cargo test builds there with the
    leg's switches. The tests include the route's, which only a candidate
    build compiles."""
    candidate = job(CANDIDATE, "candidate")
    unit = step(candidate, "run: cargo test")
    assert re.findall(r"^        run: *(.+)$", unit, re.M) == [
        'cargo test --locked --manifest-path "$RUNNER_TEMP/candidate/py/Cargo.toml"'
    ]
    assert re.findall(r"^          RUSTFLAGS: *(.+)$", unit, re.M) == ["${{ matrix.rustflags }}"]
    assert re.findall(r"^          CARGO_TARGET_DIR: *(.+)$", unit, re.M) == ["${{ runner.temp }}/candidate/target"]
    ordered = steps(candidate)
    assert ordered.index(step(candidate, "run: python run_tests.py")) < ordered.index(unit)


def test_the_rust_test_job_runs_cargo_test() -> None:
    """gwz-py's Rust unit tests (native/src), on the ordinary build."""
    rust_tests = job(CI, "rust-tests")
    unit = step(rust_tests, "run: cargo test")
    assert re.findall(r"^        run: *(.+)$", unit, re.M) == ["cargo test"]
    assert re.findall(r"^        working-directory: *(.+)$", unit, re.M) == ["gwz-py"]
    assert not re.search(r"^ +RUSTFLAGS:", rust_tests, re.M), "the tests link libpython without help"
    assert "repository: owebeeone/gwz-core\n" in rust_tests
    assert "bash .github/checkout-git2-rs.sh" in rust_tests
    assert "uses: actions/setup-python@" in rust_tests, "the tests link the interpreter's libpython"


def test_cargo_test_links_libpython_and_the_wheels_stay_extension_modules() -> None:
    """PyO3's `extension-module` feature, which PyO3 0.28's guide marks
    deprecated, stops every target linking libpython, so `cargo test` could
    not link while Cargo.toml enabled it. maturin enables it for the
    extension's builds only, from pyproject.toml's [tool.maturin] features,
    so the wheels and the editable builds are built as before; maturin 1.9.4
    and later also set PYO3_BUILD_EXTENSION_MODULE, which has the same
    effect."""
    cargo = (REPO_ROOT / "Cargo.toml").read_text(encoding="utf-8")
    [pyo3] = re.findall(r"^pyo3 = (.+)$", cargo, re.M)
    assert "extension-module" not in pyo3
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    maturin = pyproject.split("[tool.maturin]", 1)[1].split("\n[", 1)[0]
    assert re.findall(r"^features = (.+)$", maturin, re.M) == ['["pyo3/extension-module"]']
    assert re.search(r'"maturin>=1\.13,<2"', pyproject), "a maturin that sets PYO3_BUILD_EXTENSION_MODULE"


RESOLVE = "cargo metadata --format-version 1 > /dev/null"


def job_names(workflow: Path) -> list[str]:
    """The keys of `workflow`'s jobs."""
    text = workflow.read_text(encoding="utf-8").split("\njobs:\n", 1)[1]
    return re.findall(r"^  ([\w-]+):$", text, re.M)


def test_every_job_that_builds_gwz_cli_from_main_resolves_its_lock_first() -> None:
    """In a workspace gwz-cli builds in the workspace's lock, and it refreshes
    its own Cargo.lock at its releases, so the standalone lock can lag
    gwz-core's main; run_tests.py's `cargo build --locked` of the CLI then
    refuses it, as every validate job did from 2026-09-29. A job that builds
    gwz-cli's main beside gwz-core's main resolves that lock first, in CI
    only, as gwz-core's candidate job resolves its candidate manifest; the
    lock keeps every version it pins unless gwz-core's main needs another.
    The release build checks out tags, where gwz-cli pins gwz-core from
    crates.io with a lock refreshed for it, so it builds on that lock as
    committed."""
    building = []
    for workflow in (CI, CANDIDATE):
        for name in job_names(workflow):
            text = job(workflow, name)
            if "repository: owebeeone/gwz-cli\n" not in text or "run: python run_tests.py" not in text:
                continue
            building.append(name)
            resolve = step(text, RESOLVE)
            assert re.findall(r"^        working-directory: *(.+)$", resolve, re.M) == ["gwz-cli"]
            assert re.findall(r"^        shell: *(.+)$", resolve, re.M) == ["bash"], "Windows legs too"
            ordered = steps(text)
            assert ordered.index(resolve) < ordered.index(step(text, "run: python run_tests.py"))
    assert building == ["validate", "candidate"]
    assert RESOLVE not in (REPO_ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")


def test_every_job_that_builds_rust_from_main_checks_out_gwz_sspi_beside_gwz_py() -> None:
    """gwz-py and gwz-cli name gwz-sspi by path (`../gwz-sspi`), as gwz-core's
    candidate manifest does, so every job that builds Rust from main checks
    it out beside them, at its main. The publish build checks out release
    tags, whose manifests carry no path dependency (scripts/release.py)."""
    building = []
    for workflow in (CI, CANDIDATE):
        for name in job_names(workflow):
            text = job(workflow, name)
            if "uses: dtolnay/rust-toolchain@" not in text:
                continue
            building.append(name)
            checkout = step(text, "repository: owebeeone/gwz-sspi\n")
            assert re.findall(r"^          path: *(.+)$", checkout, re.M) == ["gwz-sspi"]
            assert not re.search(r"^          ref:", checkout, re.M)
    assert building == ["validate", "rust-tests", "package-smoke", "candidate"]
