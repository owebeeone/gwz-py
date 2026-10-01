from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
# gwz-py builds against the sibling gwz-core checkout (Cargo.toml path
# dependency), so the checker is always beside it, as for test_process_globals.py.
CHECKER = ROOT.parent / "gwz-core" / "scripts" / "checks" / "check_candidate_switches.py"


def test_the_switch_sites_equal_the_inventory() -> None:
    """Every site here where a candidate switch appears in a cfg is in scripts/candidate_switch_inventory.txt.

    TR2.12, rule (a) of gwz-core dev-docs/GwzTransportReleasePlanAmendment-2.md §3.13: the
    sites of `gwz_transport_candidate` and `gwz_session_candidate` equal the inventory's
    lines, and Cargo.toml's check-cfg declares both switches.
    """
    assert CHECKER.is_file(), f"gwz-core checker not found at {CHECKER}"
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--repo", str(ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
