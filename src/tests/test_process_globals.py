from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
# gwz-py builds against the sibling gwz-core checkout (Cargo.toml path
# dependency), so its source-policy checker is always beside it.
CHECKER = ROOT.parent / "gwz-core" / "scripts" / "checks" / "check_process_globals.py"
ALLOWLIST = ROOT / "scripts" / "process_globals_allowlist.json"


def test_native_layer_adds_no_process_global_state() -> None:
    """New statics, thread-locals or ambient environment reads in native/ fail here.

    Existing ones are listed with a disposition in the allowlist; entries that
    stop matching fail too, so the list only shrinks (GwzCoreSessionDesign O9).
    """
    assert CHECKER.is_file(), f"gwz-core checker not found at {CHECKER}"
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--repo", str(ROOT), "--allowlist", str(ALLOWLIST)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
