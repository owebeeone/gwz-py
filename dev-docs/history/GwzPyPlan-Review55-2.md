# GwzPyPlan Review 55-2

## Verdict

Approved With Required Fixes.

The Review48 P1/P2 blockers are now covered by the updated design and plan. One
remaining design/plan/scaffold mismatch should be fixed before parallel
implementation starts: branch-create default ownership is still inconsistent.

## Findings

1. Severity: P2
   Location: `dev-docs/GwzPyDesign.md` Public Python API lines 237-238;
   `dev-docs/GwzPyPlan.md` Phase 0 lines 119-120 and Phase 3 line 391;
   current scaffold `src/gwz/client.py` line 485
   Issue: The design and Phase 0 say branch-create defaults such as
   `start_ref="HEAD"` should be owned by `gwz-core` unless a Python compatibility
   reason is documented, but Phase 3 says the create default can remain Python
   facade behavior. The scaffold still injects `"HEAD"` client-side.
   Impact: This preserves duplicated core behavior and weakens the DRY/source of
   truth correction from Review48. Parallel agents could implement native branch
   dispatch assuming the duplication is intentional while another agent removes it
   in Phase 0.
   Recommendation: Align Phase 3 with the design: either let `None` flow to core
   for branch-create defaults, or document the explicit Python compatibility
   reason and make that exception the single accepted behavior across design,
   Phase 0, Phase 3, and tests.

2. Severity: P3
   Location: `dev-docs/GwzPyPlan.md` Phase 0 lines 121-122 and Phase 4 lines
   461-462; current scaffold `src/gwz/client.py` is 508 lines
   Issue: Phase 0 correctly says to split client helper modules if `client.py`
   remains near or above the 500-line guideline, but Phase 4 still scopes stream
   helper work only to `src/gwz/client.py`.
   Impact: This is not a blocker, but it can turn `client.py` into a facade
   chokepoint and serialize Client Agent work later.
   Recommendation: Widen Phase 4 write scope to include the future helper module
   created in Phase 0, or explicitly require Phase 0 to create the module that
   Phase 4 stream helpers will use.

3. Severity: P3
   Location: `dev-docs/GwzPyPlan.md` Phase 6 lines 589-650
   Issue: The Phase 6 roster lists Docs Agent as parallel with packaging and CLI
   agents, while the docs step correctly says README/release notes must run after
   the CLI dispatch decision is settled.
   Impact: The step text is safe, but the roster can still invite premature docs
   edits unless the coordinator serializes it.
   Recommendation: Treat Phase 6 docs as a post-decision integration step, not
   independent parallel work.

## Review48 Resolution Matrix

| Review48 item | Status | Updated location |
| --- | --- | --- |
| P1 Rust-side per-method dispatch ownership | Resolved | Design lines 297-302; Plan lines 339-357 |
| P1 Heterogeneous handler shims | Resolved | Plan lines 352-355, 405-413 |
| P1 Operation event runtime adapter | Resolved | Design lines 242-249; Plan lines 429-443 |
| P1 Full IR-driven `from_wire` decode | Resolved | Plan lines 170-182 |
| P1 Byte ABI ownership before native work | Resolved | Design lines 291-295; Plan lines 184-203, 244-251 |
| P2 Exact stream-helper set | Resolved | Design lines 242-249; Plan lines 461-469, 481-486 |
| P2 `gwz-core` release pinning and drift checks | Resolved | Design lines 171-183; Plan lines 266-277, 599-616, 652-657 |
| P2 Build backend timing | Resolved | Design lines 403-407; Plan lines 266-270, 599-602 |
| P2 Generated `client.py`/`server.py` transport conflict | Resolved | Design lines 157-162; Plan lines 97-108 |
| P2 Schema version and protocol fingerprint drift guard | Resolved | Design lines 180-183; Plan lines 159-166, 244-251, 314-321 |
| P2 Error model gaps and `GwzError` collision | Resolved | Design lines 343-365; Plan lines 222-242 |
| P3 Branch create default duplication | Partially Resolved | Design lines 237-238 and Plan lines 119-120 conflict with Plan line 391 |
| P3 Snapshot `--branch` coherence | Resolved | Design lines 40-44; Plan lines 126-128, 370-373 |
| P3 Role-out scalar methods, async/GIL, JSON codec, raise-vs-return precision | Resolved | Design lines 283-309, 355-359; Plan lines 187-190, 205-210, 237-239, 445-458 |
| P3 Phase 3 and Phase 5 serialization points | Resolved | Plan lines 339-357 and 504-517 |
| P3 Phase 1 Client Agent roster | Resolved | Plan lines 150-155 |
| P3 `clone` release gate | Resolved | Design lines 321-332; Plan lines 554-563, 574-577 |
| P3 doc/process cleanup and facade size | Partially Resolved | Design lines 74-107, 417-424; Plan lines 121-122 still need Phase 4 scope alignment |

## Design/Plan Alignment

The design and plan are aligned on the main architecture: generated taut
dataclasses and packaged IR are source of truth, CBOR is the native byte ABI,
Python does not shell out to the Rust CLI for API calls, `clone`/`forall` are
CLI-local workflows, operation events are `operation_id` based, and native Rust
owns method dispatch plus handler-shape shims.

The only material alignment gap is branch-create default ownership. Once that is
made consistent, the plan covers the design.

## Parallelism Assessment

The plan is safe and reasonably fast for parallel agents. Phase 1 now sequences
codec registry/decode/CBOR work before bridge byte transport; Phase 2 commits the
native backend and dependency model before native dispatch; Phase 3 centralizes
registry/shim ownership before operation-family modules fan out; Phase 5
serializes CLI parser/rendering infrastructure before command-family work.

The remaining parallelism cautions are narrow: do not let the Phase 3 branch
agent preserve Python-side branch defaults while Phase 0 removes them, and do not
let Phase 6 docs run before the CLI dispatch decision.

## DRY / Maintainability Assessment

Coverage is strong. The plan names shared request metadata, enum coercion,
response error handling, codec helpers, native error mapping, handler-shape
shims, backend/event-sink plumbing, CLI rendering, exit-code mapping, and shared
test fixtures. It also fixes Review48's exact stream-helper set:
`init_from_sources_stream`, `materialize_stream`, `pull_head_stream`,
`pull_snapshot_stream`, and `push_stream`.

The main DRY risk left is the branch-create `start_ref` default duplication. A
secondary maintainability risk is continuing to add helpers directly to the
already-large `client.py` instead of using the planned helper-module split.
