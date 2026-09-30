#!/usr/bin/env python3
"""Regenerate gwz-py's taut-generated Python protocol package.

The source of truth is the sibling gwz-core checkout by default:
    ../gwz-core/protocol/gwz.taut.py

Release automation can point --schema at an extracted released schema artifact.

Generation runs in child processes without PYTHONPATH. Each child imports taut
only from the taut-proto release installed in this interpreter's site
directories at the pinned version: `taut-proto` metadata or a `taut` module
anywhere else is refused.
"""

from __future__ import annotations

import argparse
import filecmp
import importlib.metadata
import json
import os
import shutil
import site
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = ROOT.parent / "gwz-core" / "protocol" / "gwz.taut.py"
DEFAULT_OUT = ROOT / "src" / "gwz" / "protocol" / "generated"
IR_NAME = "gwz.ir.json"
PYTHON_INIT = "from .api import *  # noqa: F401,F403\n"
TAUT_GENERATOR_VERSION = "0.10.0"
# A generating child: this script, loaded by path, running `child`.
CHILD = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('regen_protocol', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "raise SystemExit(module.child(sys.argv[2:]))\n"
)


def fail(message: str) -> None:
    print(f"regen_protocol: error: {message}", file=sys.stderr)
    raise SystemExit(1)


def child_env() -> dict[str, str]:
    env = dict(os.environ)
    # A sibling development checkout on PYTHONPATH must never reach generation;
    # `child` also refuses any taut that is not the pinned installed release.
    env.pop("PYTHONPATH", None)
    env.setdefault("SETUPTOOLS_SCM_PRETEND_VERSION", "0.0.0")
    env.setdefault("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_TAUT_PROTO", "0.0.0")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_child(args: list[str]) -> subprocess.CompletedProcess:
    print("+", "regen_protocol child", " ".join(args), flush=True)
    return subprocess.run(
        [sys.executable, "-c", CHILD, str(Path(__file__).resolve()), *args],
        cwd=ROOT,
        env=child_env(),
    )


def site_directories() -> set[Path]:
    """This interpreter's site directories, the only places an installed release lives."""
    directories = {Path(sysconfig.get_paths()[key]).resolve() for key in ("purelib", "platlib")}
    directories.update(Path(path).resolve() for path in site.getsitepackages())
    directories.add(Path(site.getusersitepackages()).resolve())
    return directories


def released_taut() -> Path:
    """The package directory of the installed taut-proto release this script pins."""
    try:
        distribution = importlib.metadata.distribution("taut-proto")
    except importlib.metadata.PackageNotFoundError:
        fail(
            f"taut-proto {TAUT_GENERATOR_VERSION} is required for protocol generation; "
            "none is installed"
        )
    location = Path(distribution.locate_file("")).resolve()
    if location not in site_directories():
        fail(
            f"taut-proto metadata at {location} is not the installed taut-proto release: "
            "it lies outside this interpreter's site directories"
        )
    if distribution.version != TAUT_GENERATOR_VERSION:
        fail(
            f"taut-proto {TAUT_GENERATOR_VERSION} is required for protocol generation; "
            f"found {distribution.version}"
        )
    return Path(distribution.locate_file("taut")).resolve()


def check_loaded_taut(package: Path) -> None:
    for name, module in list(sys.modules.items()):
        if name == "taut" or name.startswith("taut."):
            origin = getattr(module, "__file__", None)
            if not isinstance(origin, str) or package not in Path(origin).resolve().parents:
                fail(f"imported {name} is not the installed taut-proto release: {origin}")


def child(argv: list[str]) -> int:
    """Generate in this process, with taut from the pinned installed release only.

    `gen ...` runs tautc with those arguments; `ir SCHEMA PATH` exports the IR.
    """
    if any(name == "taut" or name.startswith("taut.") for name in sys.modules):
        fail("taut modules are already loaded; generate in a fresh interpreter")
    package = released_taut()
    import taut  # noqa: F401 -- checked before anything imports from it

    check_loaded_taut(package)
    if argv[:1] == ["ir"]:
        from taut.ir.export import schema_json
        from taut.ir.load import load_schema

        check_loaded_taut(package)
        schema = load_schema(Path(argv[1]))
        Path(argv[2]).write_text(json.dumps(schema_json(schema), indent=2) + "\n", encoding="utf-8")
        status = 0
    else:
        from taut.cli import main as tautc

        check_loaded_taut(package)
        status = tautc(argv)
    check_loaded_taut(package)
    return status


def generate_python(schema: Path, temp: Path) -> Path:
    result = run_child(["gen", str(schema), "-o", str(temp), "-l", "python", "--api-only"])
    if result.returncode != 0:
        fail("tautc Python generation failed")
    generated = temp / "python"
    if not generated.exists():
        fail(f"expected generated Python package missing: {generated}")
    return generated


def export_ir(schema: Path, path: Path) -> None:
    result = run_child(["ir", str(schema), str(path)])
    if result.returncode != 0:
        fail("taut IR export failed")


def ensure_python_package(generated: Path) -> None:
    (generated / "__init__.py").write_text(PYTHON_INIT, encoding="utf-8")


def same_tree(source: Path, dest: Path) -> bool:
    names = sorted(p.name for p in source.iterdir() if p.is_file())
    if names != sorted(p.name for p in dest.iterdir() if p.is_file()):
        return False
    return all(filecmp.cmp(source / name, dest / name, shallow=False) for name in names)


def replace_tree(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.iterdir():
        if old.is_file():
            old.unlink()
    for item in source.iterdir():
        if item.is_file():
            shutil.copyfile(item, dest / item.name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="verify only; do not write")
    args = parser.parse_args()

    schema = args.schema.resolve()
    if not schema.exists():
        fail(f"schema not found: {schema}")

    temp_root = Path(tempfile.mkdtemp(prefix="gwz-py-protocol-"))
    try:
        generated = generate_python(schema, temp_root)
        ensure_python_package(generated)
        export_ir(schema, generated / IR_NAME)
        if args.check:
            if not args.out.exists() or not same_tree(generated, args.out):
                print("regen_protocol: generated protocol files are stale", file=sys.stderr)
                return 1
            print("regen_protocol: OK")
            return 0
        replace_tree(generated, args.out)
        print(f"regen_protocol: wrote {args.out}")
        return 0
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
