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


# Spelled in two halves so that this file does not name the switch it asserts the absence of.
RETIRED_SWITCH = "gwz_windows_https_" + "qualification"
# The sites S4.5 (plan step 5.3) gives gwz-py: the platforms that construct the transport, under the candidate
# switch alone.
PLATFORMS = "all(gwz_transport_candidate, any(unix, windows))"
SCANNED = (
    "Cargo.toml",
    "build_support",
    "native",
    "pyproject.toml",
    "run_tests.py",
    "scripts",
    "src/tests",
    ".github",
)
ROUTE_SITES = (
    "native/src/route.rs",
    "native/src/client_host.rs",
    "native/src/dispatch/merge.rs",
    "native/src/dispatch/mod.rs",
)


def test_the_windows_qualification_switch_is_retired() -> None:
    """Plan step 5.3 (GwzV110Plan.md S4.5): the cfg name is gone from gwz-py's sources, manifest and scripts."""
    named = []
    for entry in SCANNED:
        base = ROOT / entry
        paths = [base] if base.is_file() else sorted(base.rglob("*"))
        for path in paths:
            if not path.is_file() or "__pycache__" in path.parts or "target" in path.parts:
                continue
            if path == Path(__file__).resolve():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if RETIRED_SWITCH in text:
                named.append(str(path.relative_to(ROOT)))
    assert named == [], f"the retired qualification switch is still named in {named}"


def test_the_route_sites_select_by_platform_and_the_candidate_switch_alone() -> None:
    for name in ROUTE_SITES:
        text = (ROOT / name).read_text(encoding="utf-8")
        assert PLATFORMS in text, name
        assert "all(windows, gwz_transport_candidate" not in text, name
        assert "all(unix, gwz_transport_candidate)" not in text, name


def test_the_candidate_switch_alone_has_no_refusal_to_compile() -> None:
    assert "compile_error!" not in (ROOT / "native/src/lib.rs").read_text(encoding="utf-8")


def test_the_native_entry_capture_precedes_the_route_capture_and_the_detach() -> None:
    """The snapshot of the caller's environment is taken at the native entry, before the GIL is released.

    gwz-core's test_windows_qualification_boundary.py kept this assertion for gwz-py while the switch was still
    named there; once the switch is gone its `ConsumerRepositories` class skips itself, and this is the assertion
    in the repository it concerns (design §2.3).
    """
    source = (ROOT / "native/src/client_host.rs").read_text(encoding="utf-8")
    network = source[source.index("fn network(") :]
    assert network.index("NativeCaller::capture") < network.index("route::capture")
    for method in ("fn call(", "fn submit("):
        body = source[source.index(method) :]
        assert body.index("self.network(") < body.index("py.detach(")
