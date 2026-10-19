# gwz-py switch tests after D31/D47 (gwz-py f7cacfb) — CODE-AXIS REVIEW
**Review object:** `git -C gwz-py show f7cacfb` (`f7cacfb3b4b68046728b20d112070c2a28b1a7b2`), which changes `scripts/candidate_switch_inventory.txt` (+2 lines) and `src/tests/test_candidate_switches.py` (the capture-order test). **Baseline:** parent `02d79cad937f2d7752362861022cd07e4d1c09a5`. The checker was run from gwz-core `e0abfe0eae7a0e11abbb6f74c19c673703a86a18`. **Date:** 2026-10-19. **Axis:** Code only. The review was read-only and is filed verbatim.

**Verdict: GO.** P0: 0, P1: 0, P2: 0, P3: 0.

## 0. Evidence base
- **Tuple at start:** gwz-py HEAD is `f7cacfb3…`, its parent is `02d79cad…`, and gwz-py's working tree is clean.
- **The diff:** `git -C gwz-py diff 02d79ca f7cacfb` touches two files, 8 insertions and 2 deletions. There are no changes under `native/` and no product code.
- **The checker:** `python3.13 gwz-core/scripts/checks/check_candidate_switches.py --repo gwz-py --list` exits 0 and prints 8 sites. They are identical, line for line and in order, to the 8 data lines of the inventory at HEAD.
- **The tests:** `.venv/bin/python -m pytest -q -p no:cacheprovider src/tests/test_candidate_switches.py` gave 6 passed in 0.06s.
- **Sources read:**
  - `native/src/client_host.rs` at f7cacfb, all of it;
  - `native/src/shims.rs` at f7cacfb, lines 144–165;
  - `native/src/client_host.rs` at `cb903bf`, before D31;
  - gwz-core `src/git/endpoint/https_worker/native/sspi.rs` at HEAD, and at `706c3852^` for the old `NativeCaller::capture`;
  - rule (a) of `gwz-core/dev-docs/history/reference/GwzTransportReleasePlanAmendment-2.md` §3.13, line 348. The path given in the brief does not exist; the file is under `history/reference/`.

## Problem, requirement provenance, and scope
- **The problem is real.** At `02d79ca` both failures follow from the source:
  - **Inventory test.** The inventory lacked the two `gwz_transport_candidate` sites that D47 added (b3e9505/c02a615). One is the `cfg_if` arm at `client_host.rs:32–33`, whose first item is `type NativeRoute`. The other is the `cfg_if` at `shims.rs:144–145`, which wraps `mod tests`. So `test_the_switch_sites_equal_the_inventory` failed.
  - **Capture-order test.** It indexed `"NativeCaller::capture"`, a string that `client_host.rs` no longer contains, so it raised ValueError. It also looked for `self.network(` before `py.detach(` in each body. Inside `call`, the first `self.network(` is now on line 194, in `scope`, which comes after `call`'s `py.detach(` on line 118. That assertion would fail as well.
- **The change is necessary.** Python's suite is part of the tested tuple the operator asked to reconcile before the push ("Reconcile Python's tested dependency tuple, then push the settled, reviewed main commits").
- **Scope is respected.** The commit changes tests and the inventory only. The inventory header's own rule says "Any change that adds, moves or removes a site updates it in the same change", so maintaining it is required. No product code changed.

## 1. Findings
None at P0–P3. The attacks below found no defect.

**Inventory lines (area 1)**
- **They equal the checker's output.** The two new lines match its `--list` output exactly.
- **They are in sorted position.**
  - `type NativeRoute` follows the two `fn network` lines in `client_host.rs`, since "f" < "t".
  - `native/src/shims.rs  mod tests` follows `native/src/route.rs` and is last.
- **Both sites are genuine.**
  - `client_host.rs:32–38`: `cfg_if! { if #[cfg(all(gwz_transport_candidate, any(unix, windows)))] { … pub(crate) type NativeRoute = gwz_core::transport_host::NativeHost; …`
  - `shims.rs:144–146`: `cfg_if! { if #[cfg(all(test, gwz_transport_candidate, any(unix, windows)))] { mod tests {`
- **Both belong under the transport switch, not `gwz_session_candidate`.** Rule (a) reserves `gwz_session_candidate` for:
  - the session host's transport entry;
  - the `server` command and its options;
  - `SocketCoreBridge`;
  - reuse;
  - the placement projection.

  Neither site is any of those:
  - `NativeRoute`/`own_backend` is the per-Client SSPI supervisor and spawn gate (C9, D31, D47). That is 1.1.0 Windows-parity transport work.
  - The `shims.rs` test checks D47's own-backend gate.

  So both are correctly under `gwz_transport_candidate`.

**Capture-order test (area 2)**
- **`self.native.caller()` is the same snapshot that `NativeCaller::capture` was.** `NativeHost::caller` (`sspi.rs:53–55`) calls `NativeCaller::captured`. Its body (`sspi.rs:106–130`) is the old `capture` body (`706c3852^`), plus one extra field, `gate: gate.clone()`:
  - `supervisor.capture_caller()` builds the `NativePort`;
  - `capture_machine_proxy()` reads the machine proxy.

  The environment snapshot is therefore the same.
- **The new assertions establish the same property.** Each link holds in the source:
  - **call and submit:** `self.scope(` comes before `py.detach(` (101 < 118 and 140 < 146).
  - **scope:** `scope` synchronously evaluates `self.network(py, …)?` on its only path, the struct field at line 194. The function has no branch.
  - **network:** `self.native.caller()` (line 211) comes before `route::capture` (line 213).

  Together these say what the old test said: the caller snapshot and the route capture both happen before the GIL is released. The added `scope` step makes no assertion weaker. Ordering within `scope` cannot matter, because `scope` contains no detach.
- **No mutant tried here passes falsely.**
  - **Functions reordered.** If `fn scope(` came after `fn network(`, the slice `source[i_scope:i_network]` would be empty and the test would fail. It cannot pass falsely. A missing name raises ValueError.
  - **`scope` removed from `call`.** `body.index("self.scope(")` would land on `submit`'s line 140, which is after `call`'s detach at line 118, so the test fails. If it is removed from `submit`, nothing follows it and the test raises ValueError.
  - **Capture moved later.** Moving `caller()` after `route::capture`, or into `Network::run`, which runs after detach, puts its first index after line 213, so the test fails.
  - **Detach before `scope`.** This fails, as the commit message says.
  - **A comment that names the substring before the real call.** This could pass falsely, but the old test had exactly the same exposure, through `NativeCaller::capture` and `self.network(` in text. It is not a weakening.
- **The non-candidate `cfg` arm takes no snapshot, as before.** The `cfg_if` around the capture (lines 210–212 and 221) has the same shape as at `cb903bf` (lines 172–174 and 182). The test reads source text, so it inspects the candidate arm whatever the build. No coverage was lost.

**Other dependents (area 3)**
- No other test in gwz-py, gwz-core `tests`/`scripts`, or gwz-cli references `NativeCaller::capture`, `self.network(` or `self.native.caller()`. `test_the_route_sites_…` checks only that the `PLATFORMS` string is present in `client_host.rs`, and that still holds.

## 2. Out-of-scope observations
- **The checkpoint's inventory digest is stale. This was already true at the baseline.**
  - **Evidence:** the inventory header says "the program checkpoint records this file's SHA-256". The latest gwz-py digest in `dev-docs/CurrentProgramCheckpoint.md` is `e943e10d…`, for 3 sites (line 1824); line 1706 has an older `2ab61cd3…`, for 2 sites. The baseline file hashes to `305d732e…` (7 sites) and HEAD's to `809c00fd…` (8 sites).
  - **Impact:** the record is out of date. No test reads it, and it does not block this object, since `02d79ca` already left it stale.
  - **Suggestion:** refresh the gwz-py digest in the checkpoint when the closeout records it.
  - **Status:** confirmed record drift; it is not a code defect.
- **Inherited limitation of the test (a hypothesis, not a regression).** Neither the old test nor the new one detects a capture wrapped in a GIL release inside `network`, for example `py.detach(|| self.native.caller())` placed before `route::capture`. A comment-based false pass is also possible, as noted above. A syntax-aware check, or a negative assertion that `network`'s slice up to `route::capture` contains no `detach(`, would close this. It is optional and does not block.
- **The brief's path is wrong.** `gwz-core/dev-docs/GwzTransportReleasePlanAmendment-2.md` has been archived to `gwz-core/dev-docs/history/reference/`. The inventory header and the test docstring still cite the old path.

## 3. Tuple check at end
gwz-py HEAD is `f7cacfb3b4b68046728b20d112070c2a28b1a7b2`, its parent is `02d79cad937f2d7752362861022cd07e4d1c09a5`, and gwz-py's working tree is clean. gwz-core is at `e0abfe0eae7a0e11abbb6f74c19c673703a86a18`. Nothing was modified.
