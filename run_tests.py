#!/usr/bin/env python3
"""gwz-py test runner. Run from the repository root with: python run_tests.py"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def run(cmd: list[str]) -> None:
    env = dict(os.environ)
    local_taut = ROOT.parent / "taut" / "src"
    paths = [str(ROOT / "src")]
    if local_taut.exists():
        paths.append(str(local_taut))
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    env.setdefault("SETUPTOOLS_SCM_PRETEND_VERSION", "0.0.0")
    env.setdefault("SETUPTOOLS_SCM_PRETEND_VERSION_FOR_TAUT_PROTO", "0.0.0")
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=ROOT, env=env)


def main() -> None:
    run([sys.executable, "scripts/regen_protocol.py", "--check"])
    run([sys.executable, "-m", "pytest", "src/tests", "-q"])


if __name__ == "__main__":
    main()
