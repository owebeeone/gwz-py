#!/usr/bin/env python3
"""Regenerate gwz-py's taut-generated Python protocol package.

The source of truth is the sibling gwz-core checkout by default:
    ../gwz-core/protocol/gwz.taut.py

Release automation can point --schema at an extracted released schema artifact.
"""

from __future__ import annotations

import argparse
import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = ROOT.parent / "gwz-core" / "protocol" / "gwz.taut.py"
DEFAULT_OUT = ROOT / "src" / "gwz" / "protocol" / "generated"
IR_NAME = "gwz.ir.json"


def fail(message: str) -> None:
    print(f"regen_protocol: error: {message}", file=sys.stderr)
    raise SystemExit(1)


def child_env() -> dict[str, str]:
    env = dict(os.environ)
    paths: list[str] = []
    local_taut = ROOT.parent / "taut" / "src"
    if local_taut.exists():
        paths.append(str(local_taut))
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    if paths:
        env["PYTHONPATH"] = os.pathsep.join(paths)
    env.setdefault("SETUPTOOLS_SCM_PRETEND_VERSION", "0.0.0")
    env.setdefault("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_TAUT_PROTO", "0.0.0")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run(cmd: list[str], *, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, env=child_env())


def generate_python(schema: Path, temp: Path) -> Path:
    result = run(
        [
            sys.executable,
            "-m",
            "taut.cli",
            "gen",
            str(schema),
            "-o",
            str(temp),
            "-l",
            "python",
        ]
    )
    if result.returncode != 0:
        fail("tautc Python generation failed")
    generated = temp / "python"
    if not generated.exists():
        fail(f"expected generated Python package missing: {generated}")
    return generated


def export_ir(schema: Path, path: Path) -> None:
    code = (
        "from pathlib import Path\n"
        "import json\n"
        "import sys\n"
        "from taut.ir.load import load_schema\n"
        "from taut.ir.export import schema_json\n"
        "schema = load_schema(Path(sys.argv[1]))\n"
        "Path(sys.argv[2]).write_text(json.dumps(schema_json(schema), indent=2) + '\\n', encoding='utf-8')\n"
    )
    result = run([sys.executable, "-c", code, str(schema), str(path)])
    if result.returncode != 0:
        fail("taut IR export failed")


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
