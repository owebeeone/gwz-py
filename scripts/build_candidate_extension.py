#!/usr/bin/env python3
"""Build gwz-py's candidate extension: the native module compiled with
`--cfg gwz_transport_candidate`, whose network operations take the transport
(1.1.0 S6.2; dev-docs/GwzPyPerOperationTransportDesign.md).

It never changes a production manifest. It makes DESTINATION, a new
directory outside the workspace, and builds there:

- core/: gwz-core's candidate manifest, which gwz-core's
  tests/transport_backend/prepare.py makes from the gwz-core checkout beside
  this one, adding gwz-transport and the transport's third-party crates;
- py/: a copy of this repository's manifest whose gwz-core dependency names
  core/, its Cargo.lock, which resolving the copy extends with the
  transport's crates, and links to the sources it builds;
- wheels/: the provisioned wheel built from py/ with RUSTFLAGS naming the
  switches, staged privately here before publication;
- target/: scratch root for unique build-owned worker and extension target
  directories, unless --target-dir names another root; private subdirectories
  are disposed after build, and the root is retained;
- extension/gwz/: the extension module, unpacked from that wheel.

The last line it prints is the module's path. The transport rows
(src/tests/test_client_host_transport.py) load the module that
GWZ_PY_NATIVE_MODULE names, and `python run_tests.py --candidate DIR` builds
one here and runs the whole suite with it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSPORT_SWITCH = "gwz_transport_candidate"
SESSION_SWITCH = "gwz_session_candidate"
# The dependency as main's Cargo.toml declares it; the release branch pins a
# registry version instead, and has no candidate build.
CORE_DEPENDENCY = 'gwz-core = { path = "../gwz-core" }'
# What maturin builds from: the manifest's library and the Python package.
SOURCES = ("native", "src", "build_support", "pyproject.toml", "README.md")
EXTENSION_SUFFIXES = (".so", ".pyd")

Run = Callable[..., object]


@dataclass(frozen=True)
class Prepared:
    destination: Path
    core: Path
    manifest: Path

    @property
    def wheels(self) -> Path:
        return self.destination / "wheels"

    @property
    def extension(self) -> Path:
        return self.destination / "extension"


def candidate_manifest(manifest: str, core: Path) -> str:
    """This repository's manifest with its gwz-core dependency naming `core`."""
    if manifest.count(CORE_DEPENDENCY) != 1:
        raise SystemExit(
            f"Cargo.toml no longer has exactly one {CORE_DEPENDENCY!r}; "
            "the candidate build names gwz-core beside this checkout"
        )
    return manifest.replace(
        CORE_DEPENDENCY, "gwz-core = { path = " + json.dumps(str(core)) + " }"
    )


def prepare(
    destination: Path,
    *,
    core: Path,
    py_root: Path = ROOT,
    python: str = sys.executable,
    run: Run = subprocess.run,
) -> Prepared:
    """Makes `destination` and its two manifests; resolves and builds nothing."""
    destination = destination.resolve()
    workspace = py_root.resolve().parent
    if destination.exists() or destination.is_relative_to(workspace):
        raise SystemExit("use a new directory outside the workspace")
    manifest = candidate_manifest(
        (py_root / "Cargo.toml").read_text(encoding="utf-8"), destination / "core"
    ).replace('path = "../gwz-sspi"', "path = " + json.dumps(str(py_root.resolve().parent / "gwz-sspi")))
    destination.mkdir(parents=True)
    run(
        [python, str(core / "tests" / "transport_backend" / "prepare.py"), str(destination / "core")],
        check=True,
    )
    py = destination / "py"
    py.mkdir()
    for name in SOURCES:
        source = py_root.resolve() / name
        (py / name).symlink_to(source, target_is_directory=source.is_dir())
    (py / "Cargo.toml").write_text(manifest, encoding="utf-8")
    (py / "Cargo.lock").write_bytes((py_root / "Cargo.lock").read_bytes())
    return Prepared(destination, destination / "core", py / "Cargo.toml")


def resolve(prepared: Prepared, *, run: Run = subprocess.run) -> None:
    """Adds the transport's crates to the copied lock, as gwz-core's candidate
    job resolves its own copy; every locked version stays as it is."""
    run(
        ["cargo", "metadata", "--format-version", "1", "--manifest-path", str(prepared.manifest)],
        check=True,
        stdout=subprocess.DEVNULL,
    )


def build_environment(
    environment: dict[str, str], *, session: bool, target_dir: Path
) -> dict[str, str]:
    switches = [TRANSPORT_SWITCH, *([SESSION_SWITCH] if session else [])]
    flags = " ".join(f"--cfg {switch}" for switch in switches)
    inherited = environment.get("RUSTFLAGS", "").strip()
    env = dict(environment)
    env["RUSTFLAGS"] = f"{inherited} {flags}" if inherited else flags
    env["CARGO_TARGET_DIR"] = str(target_dir)
    env.setdefault("SETUPTOOLS_SCM_PRETEND_VERSION", "0.0.0")
    return env


def build(
    prepared: Prepared,
    *,
    python: str = sys.executable,
    session: bool = False,
    target_dir: Path | None = None,
    run: Run = subprocess.run,
) -> Path:
    """Builds the candidate wheel and returns it."""
    prepared.wheels.mkdir(exist_ok=True)
    env = build_environment(
        dict(os.environ),
        session=session,
        target_dir=target_dir or prepared.destination / "target",
    )
    # No auditwheel repair: it would vendor the build machine's libraries into
    # gwz.libs/, which unpack() leaves behind, so the module the suite imports
    # links them where they are installed.
    run(
        [python, "build_support/sspi_backend.py", "--out", str(prepared.wheels),
         "--build-args", "--profile dev --locked --auditwheel skip"],
        check=True, cwd=prepared.manifest.parent, env=env,
    )
    wheels = sorted(prepared.wheels.glob("gwz-*.whl"), key=lambda wheel: wheel.stat().st_mtime)
    if not wheels:
        raise SystemExit(f"maturin built no gwz wheel in {prepared.wheels}")
    return wheels[-1]


def unpack(wheel: Path, extension: Path) -> Path:
    """Unpacks the wheel's extension module under `extension` and returns it."""
    with zipfile.ZipFile(wheel) as archive:
        modules = [
            name
            for name in archive.namelist()
            if name.startswith("gwz/_gwz_core.") and name.endswith(EXTENSION_SUFFIXES)
        ]
        if len(modules) != 1:
            raise SystemExit(f"{wheel.name} holds {len(modules)} gwz extension modules, not one")
        for name in archive.namelist():
            if name.startswith("gwz/"):
                destination = Path(archive.extract(name, extension))
                if destination.name in ("gwz-sspi-worker", "gwz-sspi-worker.exe"):
                    destination.chmod(0o755)
        return extension / modules[0]


def main(argv: Sequence[str] | None = None, *, run: Run = subprocess.run) -> Path:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("destination", type=Path, help="a new directory outside the workspace")
    parser.add_argument(
        "--core",
        type=Path,
        default=ROOT.parent / "gwz-core",
        help="the gwz-core checkout to build against (default: the one beside this checkout)",
    )
    parser.add_argument(
        "--session",
        action="store_true",
        help=f"build with {SESSION_SWITCH} too, as gwz-core's second candidate leg does",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="the interpreter to build for, which runs maturin (default: this one)",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        help="scratch root for unique build-owned Cargo targets (default: DESTINATION/target)",
    )
    options = parser.parse_args(argv)
    prepared = prepare(options.destination, core=options.core, python=options.python, run=run)
    resolve(prepared, run=run)
    wheel = build(
        prepared,
        python=options.python,
        session=options.session,
        target_dir=options.target_dir,
        run=run,
    )
    module = unpack(wheel, prepared.extension)
    print(module, flush=True)
    return module


if __name__ == "__main__":
    main()
