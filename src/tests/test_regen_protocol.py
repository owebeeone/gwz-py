"""Protocol regeneration uses only the pinned taut-proto release installed here."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "regen_protocol.py"

spec = importlib.util.spec_from_file_location("regen_protocol", SCRIPT)
regen_protocol = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(regen_protocol)


def _stage_taut(root: Path, *, with_metadata: bool) -> None:
    version = regen_protocol.TAUT_GENERATOR_VERSION
    if with_metadata:
        metadata = root / f"taut_proto-{version}.dist-info"
        metadata.mkdir()
        (metadata / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: taut-proto\nVersion: {version}\n"
        )
    package = root / "taut"
    package.mkdir()
    (package / "__init__.py").write_text(f"__version__ = '{version}'\n")


def _run_child(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    # The generating child process, run with a staged copy on PYTHONPATH. The
    # script itself drops PYTHONPATH for its children; this places the copy
    # where a child could otherwise find it.
    return subprocess.run(
        [sys.executable, "-c", regen_protocol.CHILD, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
    )


def test_a_different_taut_proto_release_is_refused(monkeypatch) -> None:
    monkeypatch.setattr(regen_protocol, "TAUT_GENERATOR_VERSION", "0.0.0")
    with pytest.raises(SystemExit):
        regen_protocol.released_taut()


def test_a_taut_that_shadows_the_release_is_refused(tmp_path) -> None:
    _stage_taut(tmp_path, with_metadata=False)
    result = _run_child(tmp_path, "gen", "--help")
    assert result.returncode != 0
    assert "is not the installed taut-proto release" in result.stderr


def test_a_taut_carrying_its_own_release_metadata_is_refused(tmp_path) -> None:
    # Metadata that claims the pinned release does not make a copy on
    # PYTHONPATH the installed release; only this interpreter's site
    # directories hold that.
    _stage_taut(tmp_path, with_metadata=True)
    result = _run_child(tmp_path, "gen", "--help")
    assert result.returncode != 0
    assert "is not the installed taut-proto release" in result.stderr


def test_generation_ignores_a_copy_on_pythonpath(tmp_path) -> None:
    # The script drops PYTHONPATH for the processes that generate, so a copy
    # there, even with its own metadata, cannot reach the generated protocol.
    _stage_taut(tmp_path, with_metadata=True)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert "regen_protocol: OK" in result.stdout
